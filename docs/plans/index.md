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
| [SQL-pushdown Filtering](PLAN-sql-pushdown-filtering.md) | Phase 1: Query Infrastructure | Complete | Typed criteria + generic `find_objects` primitive |
| [SQL-pushdown Filtering](PLAN-sql-pushdown-filtering.md) | Phase 2: Artifact Pushdown | Complete | Push state/namespace/name for Artifact lookups to SQL |
| [SQL-pushdown Filtering](PLAN-sql-pushdown-filtering.md) | Phase 3: Instance and Network Pushdown | Complete | Mirror Artifact pushdown for Instance and Network |
| [SQL-pushdown Filtering](PLAN-sql-pushdown-filtering.md) | Phase 4: Iterator Rework | Complete | Port iterators to single pushed-down query |
| [SQL-pushdown Filtering](PLAN-sql-pushdown-filtering.md) | Phase 5: Ad-hoc Bulk Scan Cleanup | Complete | Eliminate remaining full-table scans on filter paths |
| [SQL-pushdown Filtering](PLAN-sql-pushdown-filtering.md) | Phase 6: Tests and Documentation | Complete | Coverage and docs updates |
| [SQL-pushdown Filtering](PLAN-sql-pushdown-filtering.md) | Phase 7: Denormalised Child-UUID List Removal | Complete | Replace cached UUID lists on attributes tables with SQL queries |
| [Replace last_cluster_operation](PLAN-replace-last-cluster-operation.md) | Phase 1: `has_pending_cluster_operation` query | Planning | New query API and tests |
| [Replace last_cluster_operation](PLAN-replace-last-cluster-operation.md) | Phase 2: Switch gating callers | Planning | Move `is_okay()` and siblings off the single-pointer read |
| [Replace last_cluster_operation](PLAN-replace-last-cluster-operation.md) | Phase 3: Auto-target tracking | Planning | `*_create_and_enqueue` writes target rows automatically |
| [Replace last_cluster_operation](PLAN-replace-last-cluster-operation.md) | Phase 4: Remove explicit setters | Planning | Drop redundant `set_last_cluster_operation` callers |
| [Replace last_cluster_operation](PLAN-replace-last-cluster-operation.md) | Phase 5: Documentation and final audit | Planning | Update docs, verify CI |

### Status Definitions

- **Planning**: Design complete, implementation not yet started
- **In Progress**: Currently being implemented
- **Complete**: Implemented and released
- **Future**: Planned but not yet designed in detail
