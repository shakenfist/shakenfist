# Development Plans

This section contains forward-looking roadmaps for Shaken Fist development. These documents describe planned features and architectural directions.

!!! warning "Forward-Looking Statements"
    Plans describe intended future work and may change based on implementation experience, community feedback, or shifting priorities. Check the status table below to see what has been implemented.

## Plan Status

| Plan | Phase | Status | Description |
|------|-------|--------|-------------|
| [Blob Storage Roadmap](blob-storage-roadmap.md) | Phase 1: Hash Tracking | Complete | Move hash storage to MariaDB |
| [Blob Storage Roadmap](blob-storage-roadmap.md) | Phase 2: Lazy Dedup | Future | Composite blobs and deduplication |
| [Blob Storage Roadmap](blob-storage-roadmap.md) | Phase 3: Chunking | Future | Content-defined chunking |
| [API Query Batching](api-query-batching-roadmap.md) | Phase 1: Batch Infrastructure | Planning | Add batch query functions |
| [API Query Batching](api-query-batching-roadmap.md) | Phase 2: Prefetch Pattern | Future | Modify API to prefetch related data |
| [API Query Batching](api-query-batching-roadmap.md) | Phase 3: Generic Framework | Future | Declarative prefetch requirements |

### Status Definitions

- **Planning**: Design complete, implementation not yet started
- **In Progress**: Currently being implemented
- **Complete**: Implemented and released
- **Future**: Planned but not yet designed in detail
