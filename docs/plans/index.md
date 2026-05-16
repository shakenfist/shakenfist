# Development Plans

This section contains forward-looking roadmaps for Shaken Fist development. These documents describe planned features and architectural directions.

!!! warning "Forward-Looking Statements"
    Plans describe intended future work and may change based on implementation experience, community feedback, or shifting priorities. Check the status table below to see what has been implemented.

## Plan sequencing

The set of incomplete plans has grown to the point where the order they land in matters. The intended sequencing is:

1. **[Network operations facade](PLAN-network-facade.md)** — in progress in a separate work session. Lands first.
2. **[Retire etcd](PLAN-remove-etcd.md)** — a small, mechanical deletion sweep. Originally gated on the etcd-era cluster redeploy; that gating has been dropped because the in-place upgrade path is being closed deliberately. Lands early in the sequence so the remove-primary work below is not navigating misleading etcd references while it renames the ansible group.
3. **[Health checks, readiness, and graceful drain](PLAN-health-checks.md)** — a precondition for the BYO load-balancer story in remove-primary being operationally honest. The plan is partial: a phase 0 decisions pass resolves the open questions before the implementation phases are re-cut.
4. **[Remove the primary node](PLAN-remove-primary.md)** — the BYO-infrastructure scope reduction. Phase 7 finishes the deployer-level `etcd_master` → `database_node` rename, by which point the drain code itself is long gone. Naturally followed by a wipe-and-redeploy of Mikal's production cluster against the new shape.

The remaining incomplete plans — [Embrace TLS](PLAN-embrace-tls.md), [Sticky blob transfers](PLAN-sticky-transfers.md), and the not-yet-drafted threads for eventlog-into-MariaDB, network-node failover, and OpenTelemetry instrumentation — are intentionally **not ordered relative to each other** here. They each have specific dependencies on remove-primary having established the BYO shape (the operator-provides-PKI surface for TLS, the streaming-proxy baseline for sticky transfers, the sf-database election pattern for the others), but among themselves the order is a triage decision best made when remove-primary is close to landing rather than now.

The blob-storage and SQL-pushdown roadmaps and the network-facade plan run on their own cadence and are not part of this sequencing.

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
| [Recurring cluster operations](PLAN-recurring-operations.md) | Master plan | Stub | Cron-like framework for recurring cluster operations; absorbs `scheduled_tasks.py` and `daemons/network/maintain.py`; adds user-facing recurring tasks (e.g. snapshot every 24 hours) |
| [Health checks](PLAN-health-checks.md) | Phase 0: Research and decisions | Not started | Resolve per-daemon vs per-node endpoints, HTTP vs gRPC, readiness dependency model, drain grace, auth, mTLS interaction |
| [Health checks](PLAN-health-checks.md) | Phase 1: sf-api endpoints and drain | Not started | `/livez`, `/readyz`, `/healthz` on sf-api with SIGTERM-driven drain semantics |
| [Health checks](PLAN-health-checks.md) | Phase 2: gRPC health protocol | Not started | `grpc.health.v1.Health` on sf-database and sf-eventlog with leader / standby readiness semantics |
| [Health checks](PLAN-health-checks.md) | Phase 3: Remaining daemons | Not started | Extend the pattern to every other SF daemon |
| [Health checks](PLAN-health-checks.md) | Phase 4: Operator documentation | Not started | LB-config examples and the rolling-upgrade-with-drain procedure |
| [Remove the primary node](PLAN-remove-primary.md) | Phase 1: Remove monitoring | Not started | Drop Grafana, Prometheus, rsyslog aggregation from the deployer |
| [Remove the primary node](PLAN-remove-primary.md) | Phase 2: Bootstrap CLI | Not started | Idempotent `sf-ctl bootstrap-cluster` + `bootstrap_operations` table |
| [Remove the primary node](PLAN-remove-primary.md) | Phase 3: Remove LB | Not started | Drop the Apache reverse proxy from the deployer |
| [Remove the primary node](PLAN-remove-primary.md) | Phase 4: BYO MariaDB | Not started | Demote the local MariaDB role to opt-in dev convenience |
| [Remove the primary node](PLAN-remove-primary.md) | Phase 5: Elect sf-database | Not started | Candidate-list discovery and leader election for sf-database |
| [Remove the primary node](PLAN-remove-primary.md) | Phase 6: Galaxy role | Not started | Repackage deployer as a per-node ansible-galaxy-style role |
| [Remove the primary node](PLAN-remove-primary.md) | Phase 7: Rename and cleanup | Not started | `etcd_master` → `database_node`; final dead-code sweep |
| [Retire etcd](PLAN-remove-etcd.md) | Single sweep | Not started | Delete `shakenfist/etcd.py`, the `DATA_MIGRATIONS` drain code, residual etcd references and the `etcd3gw` dependency in one coordinated sweep |
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

- **Stub**: Framing recorded for future detailed planning; not yet ready to execute
- **Not started**: Plan exists, work not yet begun
- **Planning**: Design complete, implementation not yet started
- **In Progress**: Currently being implemented
- **Complete**: Implemented and released
- **Future**: Planned but not yet designed in detail
- **Blocked on preconditions**: Plan exists but explicitly waits on another plan or external event before work can begin
