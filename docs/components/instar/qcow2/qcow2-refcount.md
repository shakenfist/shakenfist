# QCOW2 Reference Counting System

The refcount system tracks how many times each cluster is referenced, enabling
snapshots, copy-on-write, and free space management.

## Overview

Every cluster in a QCOW2 image has an associated reference count:
- **0:** Cluster is free (available for allocation)
- **1:** Cluster is used by one reference (can modify in-place)
- **>= 2:** Cluster is shared (snapshots); must COW before writing

## Two-Level Refcount Structure

Like the L1/L2 tables, refcounts use a two-level hierarchy:

```
Cluster Index
     |
     v
+-------------------+
| Refcount Table    |  Memory-resident, one entry per refcount block
| [table_index]     |
+-------------------+
     |
     v  (refcount block offset)
+-------------------+
| Refcount Block    |  One cluster, contains many refcount entries
| [block_index]     |
+-------------------+
     |
     v
   Refcount Value
```

## Index Calculations

```c
// Configuration from header
refcount_bits = 1 << refcount_order;  // e.g., 16 bits for order=4
refcount_block_entries = cluster_size * 8 / refcount_bits;

// For cluster at byte offset 'cluster_offset'
cluster_index = cluster_offset / cluster_size;
refcount_table_index = cluster_index / refcount_block_entries;
refcount_block_index = cluster_index % refcount_block_entries;
```

### Example: 64 KB Clusters, 16-bit Refcounts

```
cluster_size = 65536 bytes
refcount_bits = 16 (refcount_order = 4)
refcount_block_entries = 65536 * 8 / 16 = 32768 entries per block

Each refcount block covers: 32768 * 64 KB = 2 GB of clusters
```

## Refcount Table Entry (64 bits)

```
 63                                              9  8            0
+------------------------------------------------+--------------+
|        Refcount Block Offset (55 bits)         |   Reserved   |
+------------------------------------------------+--------------+
                         |
                         +-- Must be cluster-aligned
```

| Bits | Name | Description |
|------|------|-------------|
| 0-8 | Reserved | Must be zero |
| 9-63 | Offset | Refcount block offset (cluster-aligned) |

**Masks:**
```c
#define REFT_OFFSET_MASK    0xfffffffffffffe00ULL
#define REFT_RESERVED_MASK  0x1ffULL
```

**Special values:**
- Entry = 0: Refcount block not allocated (all clusters free)

## Variable Refcount Widths

The `refcount_order` field (v3 only) specifies refcount entry size:

| Order | Bits | Max Value | Use Case |
|-------|------|-----------|----------|
| 0 | 1 | 1 | Single allocation bit |
| 1 | 2 | 3 | Limited snapshots |
| 2 | 4 | 15 | Limited snapshots |
| 3 | 8 | 255 | Moderate snapshots |
| 4 | 16 | 65535 | Default, many snapshots |
| 5 | 32 | 4 billion | Extreme cases |
| 6 | 64 | Unlimited | Maximum precision |

Version 2 images always use 16-bit refcounts (order = 4).

### Reading Refcount Entries

```c
uint64_t get_refcount(void *refcount_block, int index, int refcount_order) {
    int refcount_bits = 1 << refcount_order;

    switch (refcount_order) {
    case 0:  // 1-bit
        return (((uint8_t*)refcount_block)[index / 8] >> (index % 8)) & 0x1;
    case 1:  // 2-bit
        return (((uint8_t*)refcount_block)[index / 4] >> (2 * (index % 4))) & 0x3;
    case 2:  // 4-bit
        return (((uint8_t*)refcount_block)[index / 2] >> (4 * (index % 2))) & 0xf;
    case 3:  // 8-bit
        return ((uint8_t*)refcount_block)[index];
    case 4:  // 16-bit
        return be16_to_cpu(((uint16_t*)refcount_block)[index]);
    case 5:  // 32-bit
        return be32_to_cpu(((uint32_t*)refcount_block)[index]);
    case 6:  // 64-bit
        return be64_to_cpu(((uint64_t*)refcount_block)[index]);
    }
}
```

Note: Multi-byte entries are stored in big-endian format.

## Free Cluster Allocation

To find a free cluster:

```c
uint64_t find_free_cluster(BDRVQcow2State *s) {
    uint64_t cluster_index = s->free_cluster_index;

    while (1) {
        uint64_t refcount = get_refcount_for_cluster(s, cluster_index);
        if (refcount == 0) {
            s->free_cluster_index = cluster_index + 1;
            return cluster_index * s->cluster_size;
        }
        cluster_index++;
        // Check bounds...
    }
}
```

The `free_cluster_index` is a hint for where to start searching.

## Refcount Updates

When allocating or freeing clusters:

```c
int update_refcount(BlockDriverState *bs, uint64_t offset,
                    uint64_t length, int addend) {
    // For each cluster in range [offset, offset+length):
    //   1. Load refcount block (allocate if needed)
    //   2. Read current refcount
    //   3. Add 'addend' (-1 for free, +1 for alloc)
    //   4. Write new refcount
    //   5. If refcount became 0, update free_cluster_index
    //   6. If refcount became 1, may need to set COPIED flag
}
```

## Lazy Refcounts

When compatible feature bit 0 (LAZY_REFCOUNTS) is set:
- Refcount updates may be deferred
- DIRTY incompatible bit set while image is open
- On clean close, DIRTY bit cleared
- On unclean shutdown, refcounts may be inconsistent

This improves write performance at the cost of requiring `qemu-img check`
after crashes.

## Refcount Table Growth

When allocating clusters beyond current refcount table coverage:

1. Allocate new, larger refcount table
2. Allocate new refcount blocks as needed
3. Copy existing table entries
4. Update header atomically
5. Free old table

**Self-describing allocation:** New refcount structures must track themselves,
creating a chicken-and-egg problem solved by allocating at end of image and
computing refcounts for new structures.

## Relationship to COPIED Flag

The COPIED flag in L1/L2 entries is an optimization:

```
if (l2_entry & QCOW_OFLAG_COPIED) {
    // Refcount is 1, can write in-place
    write_to_cluster(offset, data);
} else {
    // May be shared, need to check refcount
    if (get_refcount(cluster) > 1) {
        // COW: allocate new cluster, copy, update L2
        new_cluster = allocate_cluster();
        copy_cluster(old_cluster, new_cluster);
        update_l2_entry(l2_index, new_cluster | QCOW_OFLAG_COPIED);
        decrement_refcount(old_cluster);
    }
    write_to_cluster(new_offset, data);
}
```

## Consistency Checking

To verify refcount consistency:

1. Build temporary refcount table by scanning all L1/L2 tables
2. For each referenced cluster, increment temporary refcount
3. Compare computed refcounts with on-disk refcounts
4. Verify COPIED flags match actual refcounts

```
qemu-img check image.qcow2
```

## Repairing Refcount Inconsistencies

`instar check --repair[=leaks|all]` repairs the refcount inconsistencies the
consistency check above detects, in place, mirroring `qemu-img check -r
leaks`/`-r all`. The read-only `instar check` is the safe default; `--repair`
is opt-in and writes to the image. qcow2 only.

### The two tiers

**`leaks` (safe, lossless — the bare `--repair` default).** Frees every
cluster the integrity walk proved allocated-but-unreferenced (refcount > 0 but
no L2 entry references it) by setting its refcount to 0. It never lowers a
*referenced* cluster's refcount, even when that cluster is over-counted —
correcting an over-count requires a full recount and is the lossy tier's
concern. Setting a leaked entry to 0 is a single monotonic write and is
crash-safe on its own; no `corrupt`-bit dance is needed.

**`all` (lossy).** Rebuilds the refcount structure against a computed
reference count and reconciles the COPIED flags. For the supported scope —
snapshot-free, uncompressed, single-file images with no other detected
corruption — every cluster's correct refcount is exactly 0 or 1, so the
detection bitmap *is* the computed refcount (no separate counting walk is
needed). Per refcount block it raises under-counts, lowers over-counts, and
frees zero-counts (so it subsumes the leaks tier), then reconciles each L1/L2
entry's `OFLAG_COPIED` bit to match — set if and only if the referenced
cluster's refcount is exactly 1.

### Crash-safe write ordering

The lossy tier rewrites refcounts and COPIED flags, so an interrupted run must
never leave a silently-inconsistent image presenting as clean. It writes under
the `INCOMPAT_CORRUPT` header bit (incompatible-features field, header offset
72), in four fsync-separated steps:

1. Set `INCOMPAT_CORRUPT`; fsync.
2. Correct refcounts per refcount block; fsync.
3. Reconcile `OFLAG_COPIED` over the active L1 table and its L2 tables; fsync.
4. Clear `INCOMPAT_CORRUPT`; fsync.

A failure at any point aborts and leaves the `corrupt` bit **set**: the image
refuses read-write open until it is re-repaired, rather than presenting as
clean while half-written. The leaks tier needs none of this — freeing an
unreferenced cluster is a single monotonic refcount write.

### Refuse rather than guess

The lossy `all` tier declines its rebuild — reporting the result incomplete —
when the correct fix is not mechanically determined by the rest of the
metadata. The safe `leaks` reclamation is lossless, so it still runs in these
cases (the image is only guaranteed byte-identical for snapshotted images,
where both tiers refuse):

- **Snapshotted images** (`nb_snapshots > 0`) — the detection walk does not
  traverse snapshot L1/L2 tables, so a cluster referenced only by an internal
  snapshot looks unreferenced; freeing it would corrupt the snapshot. This
  guard refuses **both** tiers (it takes precedence over the `all` tier), so a
  snapshotted image is left byte-identical.
- **Compressed and external-data images** (zstd `INCOMPAT_COMPRESSION`,
  `INCOMPAT_EXTERNAL_DATA`, or any `OFLAG_COMPRESSED` cluster) — shared
  compressed host clusters and the COPIED-on-compressed rule fall outside the
  bitmap-as-count model.
- **Already-`corrupt`-flagged images** (any other detected corruption) — the
  recount identity holds only when no corruption is present.
- **Refcount-table exhaustion** — v1 does not grow the refcount table; an
  out-of-space repair is reported, never worked around.
- **Structural overlaps** (two L2 entries → one host cluster) — the leaks tier
  reclaims any genuine leak but leaves the overlap in place: a safe *partial*
  repair, reported incomplete.

The safe (`leaks`) tier is deliberately narrower than `qemu-img check -r
leaks`, which also trims over-counts; see
[../quirks.md](/components/instar/quirks/#check---repairleaks-scope-vs-qemu-img-check--r-leaks).

## Refcount Metadata Sizing

```c
// Calculate metadata requirements
int64_t clusters = (disk_size + cluster_size - 1) / cluster_size;
int64_t refcount_block_entries = cluster_size * 8 / refcount_bits;
int64_t refcount_blocks = (clusters + refcount_block_entries - 1)
                          / refcount_block_entries;
int64_t refcount_table_entries = refcount_blocks;
int64_t refcount_table_size = refcount_table_entries * 8;

// Don't forget refcount blocks for metadata clusters themselves!
```

## Snapshot Refcount Interaction

When creating a snapshot:
1. Copy current L1 table to snapshot
2. Increment refcounts for all referenced clusters (+1)
3. Clear COPIED flags on shared clusters

When deleting a snapshot:
1. Decrement refcounts for all snapshot-referenced clusters (-1)
2. Free clusters that reach refcount 0
3. Set COPIED flags where refcount becomes 1

## Implementation Notes

1. **Caching:** Refcount blocks should be cached; frequent lookups during I/O
2. **Atomic updates:** Table updates must be atomic (write new, update header)
3. **Overflow:** Check for refcount overflow before incrementing
4. **Underflow:** Decrementing refcount 0 indicates corruption
5. **Alignment:** All refcount structures must be cluster-aligned
