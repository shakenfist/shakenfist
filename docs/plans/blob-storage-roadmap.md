# Blob Storage Roadmap

This document describes the planned evolution of Shaken Fist's blob storage system, from the current etcd-based hash tracking to a fully content-addressable, deduplicated storage layer with support for content-defined chunking.

## Vision

The end state is a storage system where:

- **Content-addressable**: Blobs can be looked up by hash in O(1) time
- **Deduplicated**: Identical content is stored once, regardless of how many blobs reference it
- **Chunked**: Large blobs are broken into content-defined chunks that can be shared across blobs
- **Efficient**: Hash lookups, duplicate detection, and verification are all database-indexed operations

```
End state architecture:

Instance → Blob A (composite) → [Chunk1, Chunk2, Chunk3]
Artifact → Blob B (composite) → [Chunk1, Chunk4, Chunk5]
                                    ↑
                            Shared chunk (stored once)
```

## The state before any changes

Before this work, blob storage had several limitations:

- **Hash storage in etcd**: Hashes stored at `attribute/blob/{uuid}/checksums` with O(n) lookup
- **No deduplication**: Two users uploading identical content get two copies on disk
- **Separate tracking systems**: Hashes in etcd, locations in `object_references` table
- **Brute-force hash lookup**: Finding a blob by hash iterates through ALL blobs

Key code locations:
- Hash verification: `shakenfist/blob.py:915-977`
- Hash lookup API: `shakenfist/external_api/blob.py:345-358`
- Scheduled verification: `shakenfist/daemons/cluster/scheduled_tasks.py:95-111`

## Roadmap

### Phase 1: Hash Tracking in MariaDB

**Goal**: Move hash storage from etcd to MariaDB with proper indexes, enabling O(1) hash lookups.

**Status**: Complete

**Prerequisites**: PLAN-reference-counts.md (COMPLETE)

**What it enables**:
- O(1) "find blob by hash" queries via `idx_hash_lookup` index
- Proper separation of hash values from verification metadata
- Foundation for duplicate detection in Phase 2
- Support for additional hash algorithms (BLAKE3, etc.)

#### Schema

```sql
CREATE TABLE blob_hashes (
    blob_uuid UUID NOT NULL,
    node VARCHAR(255) NOT NULL,
    algorithm VARCHAR(32) NOT NULL,
    hash_value VARCHAR(256) NOT NULL,
    file_size BIGINT NOT NULL,
    computed_at TIMESTAMP NOT NULL,
    last_verified_at TIMESTAMP NOT NULL,
    verification_status VARCHAR(16) NOT NULL DEFAULT 'pending',
    error_message TEXT,
    PRIMARY KEY (blob_uuid, node, algorithm),
    INDEX idx_blob_status (blob_uuid, verification_status),
    INDEX idx_node (node),
    INDEX idx_last_verified (last_verified_at),
    INDEX idx_status (verification_status),
    INDEX idx_hash_lookup (algorithm, hash_value)
);
```

#### Migration Strategy

Hard cutover using `sf-client migrate-checksums-to-mariadb`:

1. Run migration command to copy existing checksums from etcd to MariaDB
2. Deploy updated code that reads/writes only to MariaDB
3. Delete old `checksums` attributes from etcd

The migration command is idempotent and can be run multiple times safely.

#### Implementation Steps

1. Create Pydantic schema for BlobHash
2. Add MariaDB table and access functions (following `object_references` patterns)
3. Add gRPC handlers in database daemon
4. Create migration command
5. Update `verify_checksum()` to write to MariaDB
6. Update `checksums` property to read from MariaDB
7. Remove etcd checksum code

---

### Phase 2: Composite Blobs and Lazy Deduplication

**Goal**: Enable storage deduplication by converting duplicate blobs to composites that reference shared content.

**Status**: Not started

**Prerequisites**: Phase 1 (blob_hashes table with `idx_hash_lookup`)

**What it enables**:
- Storage savings when identical content is uploaded multiple times
- Incremental deduplication (happens lazily, no big migration)
- Foundation for content-defined chunking in Phase 3

#### Key Concept: Composite Blobs

Add a `type` field to blobs: `content` (default) or `composite`. A composite blob's content is defined by references to other blobs (chunks).

```
Before deduplication:
  Blob A (type=content, 10GB file)  ←── Instance disk
  Blob B (type=content, 10GB file)  ←── Artifact index
  # Identical content, stored twice

After lazy dedup detects A.hash == B.hash:
  Blob A (type=composite) → [C]     ←── Instance disk (resolves to C)
  Blob B (type=composite) → [C]     ←── Artifact index (resolves to C)
  Blob C (type=content, 10GB file)  ←── System-owned content blob
  # Only one copy on disk
```

#### Ownership Model

Content blobs (type=content) are always system-owned. User-facing blobs are composites that reference shared content:

- When User A uploads content, system creates content blob C (system-owned) and composite blob A (user-owned) pointing to C
- When User B uploads identical content, system reuses C and creates composite blob B (user-owned) pointing to C
- When User A deletes blob A, composite A is deleted, C's ref_count decreases
- Content blob C only deleted when ref_count hits 0 (no composites reference it)

This cleanly separates user identity/permissions (on composite blobs) from content storage (on system-owned content blobs).

#### How Deduplication Works

1. Lazy hashing computes hashes for blobs (existing behavior)
2. `idx_hash_lookup` finds blobs with matching hashes
3. Create system-owned content blob (or reuse existing)
4. Convert user blobs to composites pointing to content blob
5. Delete duplicate files, keep only one copy

#### Chunk Storage

Chunk references stored in `object_references` table:

```sql
-- RelationshipType.CHUNK added to enum
source_type=BLOB, source_uuid=composite_blob,
relationship=CHUNK, relationship_value="0",
target_type=BLOB, target_uuid=content_blob
```

This provides:
- "What chunks make up blob X?" → `get_references_from(BLOB, X, relationship=CHUNK)`
- "What blobs use chunk C?" → `get_references_to(BLOB, C, relationship=CHUNK)`
- Ref counting works naturally - chunk can't be deleted while referenced

---

### Phase 3: Content-Defined Chunking

**Goal**: Break large blobs into variable-sized chunks based on content boundaries, enabling cross-blob deduplication.

**Status**: Not started

A prototype exists in `src/private/flywheel`.

**Prerequisites**: Phase 2 (composite blobs)

**What it enables**:
- Deduplication at sub-blob granularity
- Efficient storage of similar but not identical images
- Reduced transfer sizes for incremental updates

#### Key Concept

Instead of storing a 10GB VM image as one blob, break it into ~1000 chunks of ~10MB each based on content boundaries (using algorithms like FastCDC with BLAKE3 hashing).

```
Blob X (composite) → [C1, C2, C3, C4, ...]  ←── Ubuntu 22.04 image
Blob Y (composite) → [C1, C2, C5, C6, ...]  ←── Ubuntu 22.04 with customizations
                       ↑   ↑
                 Shared chunks (base OS)
```

#### Implementation Notes

- Uses same composite blob infrastructure from Phase 2
- Chunks are just content blobs referenced via CHUNK relationships
- BLAKE3 hashing for efficient chunk identification (schema already supports it)
- Content-defined chunking only for new uploads; existing blobs can be lazily re-chunked

---

## Design Decisions

### Why Composite Blobs Instead of a Separate ContentStore?

We considered several approaches to deduplication:

| Approach | Description | Tradeoffs |
|----------|-------------|-----------|
| **A: Blob → ContentStore** | Two-layer: Blob (UUID) references ContentStore (hash) | Clean separation, but requires new object type and migration |
| **B: Hash as Identity** | Blobs identified by hash, not UUID | Matches Docker, but breaking API change |
| **C: Defer** | Keep current model, revisit later | Safe, but delays benefits |
| **D: Composite Blobs** | Single type with `content` or `composite` flag | Incremental, API-compatible, unifies dedup and chunking |

**Decision**: Option D (Composite Blobs)

Key insight: Option D achieves Option A's architecture using a single object type. Chunks *are* blobs - they just play a different role (system-owned, only referenced via CHUNK relationships, hold actual bytes).

```
Option A: Blob (public) → ContentStore (internal, new type)
Option D: Blob (composite, user-owned) → Blob (content, system-owned)
```

This allows incremental migration:
- Day 1: All blobs are `type=content` (current state)
- Day N: Lazy dedup converts some to `type=composite`
- Day M: Content-defined chunking creates composites from the start
- End: Most user-facing blobs are composites, most storage is in shared chunks

### Why Store Chunks in object_references?

We considered three options for chunk list storage:

| Option | Storage | Pros | Cons |
|--------|---------|------|------|
| File content | Composite blob file = JSON manifest | Consistent | Two reads minimum |
| Blob attribute | etcd/MariaDB metadata | Single read | Large for many chunks; etcd scaling |
| **object_references** | CHUNK relationship type | Queryable; ref counting works | Another relationship type |

**Decision**: Use `object_references` table

This avoids creating another "two storage locations" problem and leverages infrastructure already built for PLAN-reference-counts.md.

### Why VARCHAR Instead of ENUM for Status?

The `verification_status` column uses `VARCHAR(16)` instead of `ENUM('valid', 'invalid', 'pending')` because ENUMs are painful to modify in MySQL. Using VARCHAR allows adding states like `'quarantined'` or `'verifying'` without schema migration.

---

## Open Questions

### REST API Hash References

Should the REST API accept hash-based references? e.g., `GET /blob/sha512:abc123...`

This would enable Docker-style content-addressable access but requires:
- Streaming hash computation on upload
- Decision on what to return if multiple composites share the same content

### Chunk Size Strategy

For content-defined chunking, what's the optimal target chunk size?
- Smaller chunks = more deduplication, more metadata overhead
- Larger chunks = less deduplication, simpler management
- Need benchmarking with real workloads

