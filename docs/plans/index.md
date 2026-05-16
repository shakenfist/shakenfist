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
| [Replace last_cluster_operation](PLAN-replace-last-cluster-operation.md) | Phase 1: `has_pending_cluster_operation` query | Complete | New query API and tests |
| [Replace last_cluster_operation](PLAN-replace-last-cluster-operation.md) | Phase 2: Switch gating callers | Complete | Move `is_okay()` and siblings off the single-pointer read |
| [Replace last_cluster_operation](PLAN-replace-last-cluster-operation.md) | Phase 3: Auto-target tracking | Complete | `*_create_and_enqueue` writes target rows automatically |
| [Replace last_cluster_operation](PLAN-replace-last-cluster-operation.md) | Phase 4: Remove explicit setters | Complete | Drop redundant `set_last_cluster_operation` callers |
| [Replace last_cluster_operation](PLAN-replace-last-cluster-operation.md) | Phase 5: Documentation and final audit | Complete | Update docs, verify CI |
| [Fix cluster_operation_targets UNIQUE constraint](PLAN-fix-cluster-operation-targets-unique-constraint.md) | Schema fix | Complete | Replace column-level `UNIQUE(operation_uuid)` with composite `UNIQUE(operation_uuid, target_object_type, target_uuid)` so multi-target ops record all their target rows |
| [Network operations facade](PLAN-network-facade.md) | Master plan | Planning | Split `Network` into a queue-enqueuing facade and a single-mutator worker so local daemons can no longer bypass `net-worker`'s serialisation |
| [Remove the primary node](PLAN-remove-primary.md) | Phase 1: Remove monitoring | Not started | Drop Grafana, Prometheus, rsyslog aggregation from the deployer |
| [Remove the primary node](PLAN-remove-primary.md) | Phase 2: Bootstrap CLI | Not started | Idempotent `sf-ctl bootstrap-cluster` + `bootstrap_operations` table |
| [Remove the primary node](PLAN-remove-primary.md) | Phase 3: Remove LB | Not started | Drop the Apache reverse proxy from the deployer |
| [Remove the primary node](PLAN-remove-primary.md) | Phase 4: BYO MariaDB | Not started | Demote the local MariaDB role to opt-in dev convenience |
| [Remove the primary node](PLAN-remove-primary.md) | Phase 5: Elect sf-database | Not started | Candidate-list discovery and leader election for sf-database |
| [Remove the primary node](PLAN-remove-primary.md) | Phase 6: Galaxy role | Not started | Repackage deployer as a per-node ansible-galaxy-style role |
| [Remove the primary node](PLAN-remove-primary.md) | Phase 7: Rename and cleanup | Not started | `etcd_master` → `database_node`; final dead-code sweep |
| [Embrace TLS](PLAN-embrace-tls.md) | Phase 0: Research and decisions | Not started | Resolve open TLS questions into a decisions document |
| [Embrace TLS](PLAN-embrace-tls.md) | Phase 1: Cert reload | Not started | Graceful TLS material reload across daemons |
| [Embrace TLS](PLAN-embrace-tls.md) | Phase 2: sf-database mTLS | Not started | Canary mTLS path for the highest-traffic gRPC channel |
| [Embrace TLS](PLAN-embrace-tls.md) | Phase 3: Other gRPC mTLS | Not started | Extend mTLS to the remaining inter-daemon channels |
| [Embrace TLS](PLAN-embrace-tls.md) | Phase 4: MariaDB TLS | Not started | TLS on the SF-to-MariaDB connection |
| [Embrace TLS](PLAN-embrace-tls.md) | Phase 5: sf-api TLS | Not started | Optional native TLS on sf-api; document operator-LB story |
| [Embrace TLS](PLAN-embrace-tls.md) | Phase 6: Expiry monitoring | Not started | Cert expiry warnings as events + prometheus metrics |
| [Embrace TLS](PLAN-embrace-tls.md) | Phase 7: Dev CA | Not started | Repurpose `pki_internal_ca` as dev/test convenience only |
| [Sticky blob transfers](PLAN-sticky-transfers.md) | Phase 0: Research and decisions | Not started | Resolve cookie format, LB coverage, and placement-interaction questions |
| [Sticky blob transfers](PLAN-sticky-transfers.md) | Phase 1: Server-side cookies | Not started | sf-api emits and honours server-set sticky cookies |
| [Sticky blob transfers](PLAN-sticky-transfers.md) | Phase 2: LB documentation | Not started | Document HAProxy / Envoy / cloud-LB / nginx configurations |
| [Sticky blob transfers](PLAN-sticky-transfers.md) | Phase 3: Client verification | Not started | Verify SF Python client cookie handling end-to-end |
| [Sticky blob transfers](PLAN-sticky-transfers.md) | Phase 4: Failover behaviour | Not started | Define recovery path when the sticky backend dies mid-session |

### Status Definitions

- **Planning**: Design complete, implementation not yet started
- **In Progress**: Currently being implemented
- **Complete**: Implemented and released
- **Future**: Planned but not yet designed in detail
