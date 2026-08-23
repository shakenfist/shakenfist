# API Query Batching Roadmap

This document describes planned optimizations for REST API endpoints that list
objects, addressing the N+1 query problem that affects performance when
returning large result sets.

## Problem Statement

Many REST API endpoints that return lists of objects suffer from an N+1 query
problem. When listing N objects, each object's `external_view()` method makes
one or more additional database queries to fetch related data. This results in
poor performance that scales linearly with result set size.

### Example: Blob Listing

When listing blobs via the REST API:

1. Query returns N blob records from etcd/MariaDB
2. For each blob, `external_view()` calls `get_valid_checksums(blob_uuid)`
3. Each `get_valid_checksums()` makes a separate database query
4. Total queries: 1 + N

With 100 blobs, this means 101 database round-trips. With content-defined
chunking (where blob counts could reach thousands), this becomes a significant
bottleneck.

### Affected Endpoints

The N+1 pattern likely affects any endpoint that:

- Lists objects with `external_view()` rendering
- Fetches related data (checksums, references, metadata) per object
- Returns paginated results where page size can be large

Known affected areas:

- **Blob listings**: Each blob fetches checksums from `blob_hashes` table
- **Instance listings**: May fetch network info, disk info per instance
- **Artifact listings**: May fetch version/blob info per artifact

## Proposed Solution

### Phase 1: Batch Query Infrastructure

Add batch query functions to the database layer that can fetch related data
for multiple objects in a single query.

```python
# Example batch function signature
def get_valid_checksums_batch(
    blob_uuids: list[str]
) -> dict[str, dict[str, str]]:
    """Get valid checksums for multiple blobs in a single query.

    Args:
        blob_uuids: List of blob UUIDs to fetch checksums for.

    Returns:
        Dict mapping blob_uuid to dict of algorithm -> hash_value.
    """
    # Single query: SELECT * FROM blob_hashes
    #               WHERE blob_uuid IN (:uuids)
    #               AND verification_status = 'valid'
```

### Phase 2: Prefetch Pattern in API Layer

Modify API endpoints to prefetch related data before rendering objects.

```python
# Before (N+1 queries):
blobs = get_blobs()
return [b.external_view() for b in blobs]

# After (2 queries):
blobs = get_blobs()
blob_uuids = [str(b.uuid) for b in blobs]
checksums = mariadb.get_valid_checksums_batch(blob_uuids)
return [b.external_view(prefetched_checksums=checksums.get(str(b.uuid)))
        for b in blobs]
```

### Phase 3: Generic Prefetch Framework

Consider a more generic approach where `external_view()` can declare what
related data it needs, and the API layer automatically batches those fetches.

```python
class Blob(DatabaseBackedObject):
    @classmethod
    def prefetch_requirements(cls) -> list[PrefetchSpec]:
        return [
            PrefetchSpec(
                name='checksums',
                fetcher=mariadb.get_valid_checksums_batch,
                key_extractor=lambda b: str(b.uuid)
            )
        ]
```

### Phase 4: Push audit

Run the checks in `PUSH-AUDIT.md` over the accumulated diff of every phase in
this roadmap against `develop`, not the last phase's diff alone -- what the
phases did to each other is only visible in the whole. Findings land as their
own pull request, and this roadmap is not complete until each is resolved or
declined in writing here. If the audit finds nothing, that is recorded in one
sentence.

## Alternative Approaches Considered

### Denormalized Cache Tables

For frequently-accessed data like valid checksums, a denormalized table could
reduce query complexity:

```sql
CREATE TABLE valid_checksums (
    blob_uuid UUID NOT NULL,
    algorithm VARCHAR(32) NOT NULL,
    hash_value VARCHAR(256) NOT NULL,
    PRIMARY KEY (blob_uuid, algorithm)
);
```

**Pros:**

- Simpler queries (no filtering by status needed)
- Smaller result sets (no node/timestamp fields)

**Cons:**

- Must be kept in sync with source table
- Adds write overhead when checksums are updated
- Still requires batching to solve N+1

**Verdict:** Could be combined with batching for maximum benefit, but batching
alone solves the core problem.

### GraphQL / DataLoader Pattern

A more radical approach would be adopting GraphQL with DataLoader-style
batching, which automatically coalesces related queries.

**Verdict:** Too large a change for the current architecture. Batching within
the existing REST API is more pragmatic.

## Implementation Order

1. **Blob checksums batching** (highest impact due to blob count scaling)
2. **Audit affected endpoints** (identify other N+1 patterns)
3. **Generic framework** (if patterns are common enough to warrant it)

## Success Metrics

- Blob listing API response time should not scale linearly with result count
- Database query count for listing N objects should be O(1) not O(N)

## Dependencies

- None (can be implemented independently)

## Related Work

- [Blob Storage Roadmap](blob-storage-roadmap.md) - Phase 1 introduced the
  `blob_hashes` table that exposed this N+1 pattern
