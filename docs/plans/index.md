# Development Plans

This section contains forward-looking roadmaps for Shaken Fist development. These documents describe planned features and architectural directions.

!!! warning "Forward-Looking Statements"
    Plans describe intended future work and may change based on implementation experience, community feedback, or shifting priorities. Check the status table below to see what has been implemented.

## Plan sequencing

The set of incomplete plans has grown to the point where the order they land in matters. The intended sequencing is:

1. **[Network operations facade](PLAN-network-facade.md)** — complete. Landed via the `network-facade` branch.
2. **Retire etcd** — _(absorbed into [BYO MariaDB and sf-database tier](PLAN-byo-mariadb.md) phase 0; the standalone `PLAN-remove-etcd.md` has been removed.)_ Inspection confirmed the etcd data drain is complete (the `DATA_MIGRATIONS` dict is empty), but the supporting machinery — `shakenfist/etcd.py`, `etcd3gw`, the etcd proto stubs, the drain test files, `is_etcd_master`, the migration-era `sf-ctl` aliases — is still in tree. Phase 0 of the BYO-MariaDB plan deletes it as a single sweep. The `etcd_master` ansible group name rename stays with `PLAN-remove-primary` phase 7 as a deploy-scope concern.
3. **[Health checks, readiness, and graceful drain](PLAN-health-checks.md)** — a precondition for the BYO load-balancer story in remove-primary being operationally honest. The plan is partial: a phase 0 decisions pass resolves the open questions before the implementation phases are re-cut.
4. **[Remove the primary node](PLAN-remove-primary.md)** — the BYO-infrastructure scope reduction. Phase 7 finishes the deployer-level `etcd_master` → `database_node` rename, by which point the drain code itself is long gone. Naturally followed by a wipe-and-redeploy of Mikal's production cluster against the new shape.
5. **[BYO MariaDB and `sf-database` as a tier](PLAN-byo-mariadb.md)** — lifted out of remove-primary because it grew into its own master plan. Removes MariaDB-server install from the deployer entirely, reshapes `sf-database` into a deployer-chosen tier of equal stateless instances reached via client-side gRPC load balancing (not leader election), and carves schema/migration execution out of daemon startup into an operator-run `sf-ctl ensure-mariadb-schema` command. Can land in parallel with remove-primary's remaining phases; its phase 1 also performs the scope-shift edit to remove-primary itself.

The remaining incomplete plans — [Embrace TLS](PLAN-embrace-tls.md), [Sticky blob transfers](PLAN-sticky-transfers.md), [Replace exec'd network commands with netlink](PLAN-replace-exec-with-netlink.md), [Atomic scheduling via reservations](PLAN-scheduler-reservations.md), the connected [Generic allocator](PLAN-generic-allocator.md) / [Network service ports](PLAN-network-service-ports.md) / [Network carrier model](PLAN-network-carrier-model.md) triple, and the not-yet-drafted OpenTelemetry instrumentation thread — are intentionally **not ordered relative to each other** here. They each have specific dependencies on either remove-primary having established the BYO shape (the operator-provides-PKI surface for TLS, the streaming-proxy baseline for sticky transfers, the sf-database election pattern for the others) or network-facade having landed (the netlink plan, whose privilege-separation phases need network-facade's single-mutator property), but among themselves the order is a triage decision best made when remove-primary is close to landing rather than now. The scheduler-reservations plan is independent of the BYO shape but benefits from the OpenTelemetry thread landing first so that phase 0's design choices can be informed by real load and contention numbers.

The **generic-allocator / network-service-ports / network-carrier-model triple** is internally ordered. Generic-allocator is the foundational refactor (replaces five ad-hoc allocators with one primitive and is independently shippable). Network-service-ports builds on the allocator to expose per-network DNAT'd ports for managed services (web consoles, transfer agents, managed VPN endpoints). Network-carrier-model layers a smeared lease-based per-network carrier role with VIP advertisement on top, removing the network-node singleton; it depends on both prior plans and is the largest of the three. The triple supersedes the "network node failover" thread that was previously a not-yet-drafted line item.

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
| [Network operations facade](PLAN-network-facade.md) | Master plan | Complete | Split `Network` into a queue-enqueuing facade and a single-mutator worker so local daemons can no longer bypass `net-worker`'s serialisation |
| [Queue performance and coalescing](PLAN-queue-performance.md) | Steps 1-6 | In Progress | Unified batched dequeue, coalescible-task metadata, worker- and enqueue-side dedup of redundant cluster operations. Step 7 (measure with CI data, decide on fairness) outstanding. |
| [Recurring cluster operations](PLAN-recurring-operations.md) | Master plan | Stub | Cron-like framework for recurring cluster operations; absorbs `scheduled_tasks.py` and `daemons/network/maintain.py`; adds user-facing recurring tasks (e.g. snapshot every 24 hours) |
| [Health checks](PLAN-health-checks.md) | Phase 0: Research and decisions | Not started | Resolve per-daemon vs per-node endpoints, HTTP vs gRPC, readiness dependency model, drain grace, auth, mTLS interaction |
| [Health checks](PLAN-health-checks.md) | Phase 1: sf-api endpoints and drain | Not started | `/livez`, `/readyz`, `/healthz` on sf-api with SIGTERM-driven drain semantics |
| [Health checks](PLAN-health-checks.md) | Phase 2: gRPC health protocol | Not started | `grpc.health.v1.Health` on sf-database with leader / standby readiness semantics |
| [Health checks](PLAN-health-checks.md) | Phase 3: Remaining daemons | Not started | Extend the pattern to every other SF daemon |
| [Health checks](PLAN-health-checks.md) | Phase 4: Operator documentation | Not started | LB-config examples and the rolling-upgrade-with-drain procedure |
| [Remove the primary node](PLAN-remove-primary.md) | Phase 1: Remove monitoring | Not started | Drop rsyslog aggregation from the deployer (Grafana and the primary-node Prometheus server already removed as warmup) |
| [Remove the primary node](PLAN-remove-primary.md) | Phase 2: Bootstrap CLI | Not started | Idempotent `sf-ctl bootstrap-cluster` + `bootstrap_operations` table |
| [Remove the primary node](PLAN-remove-primary.md) | Phase 3: Remove LB | Not started | Drop the Apache reverse proxy from the deployer |
| [Remove the primary node](PLAN-remove-primary.md) | Phases 4-5: _(moved to BYO MariaDB)_ | _(moved)_ | MariaDB BYO and `sf-database`-as-tier lifted into [PLAN-byo-mariadb.md](PLAN-byo-mariadb.md) |
| [Remove the primary node](PLAN-remove-primary.md) | Phase 6: Galaxy role | Not started | Repackage deployer as a per-node ansible-galaxy-style role |
| [Remove the primary node](PLAN-remove-primary.md) | Phase 7: Rename and cleanup | Not started | `etcd_master` → `database_node`; final dead-code sweep |
| [BYO MariaDB and sf-database tier](PLAN-byo-mariadb.md) | [Phase 0: Retire etcd machinery](PLAN-byo-mariadb-phase-00-retire-etcd.md) | Complete | Supersedes `PLAN-remove-etcd`; deleted `etcd.py`, `etcd3gw`, etcd protos, `DATA_MIGRATIONS` framework, drain tests, dead `sf-ctl` helpers and `show/set-etcd-config` aliases, stale `migrate-*` comments, `.claude/skills/migrate-etcd-to-mariadb.md` (`is_etcd_master` deferred to `PLAN-remove-primary` phase 7) |
| [BYO MariaDB and sf-database tier](PLAN-byo-mariadb.md) | [Phase 1: Statelessness and scope shift](PLAN-byo-mariadb-phase-01-statelessness.md) | Complete | Schema-versions lock; stop sf-database calling `ensure_schema()` at startup; daemon startup-version check; MariaDB compat check (version/engine/charset) on `ensure-mariadb-schema` and sf-database; lift scope out of remove-primary |
| [BYO MariaDB and sf-database tier](PLAN-byo-mariadb.md) | [Phase 2: Config untangle](PLAN-byo-mariadb-phase-02-config-untangle.md) | Complete | Untangle `MARIADB_HOST` from "I am the database node"; rename `DATABASE_NODE_IP` → `MARIADB_GATEWAY_HOSTS` (plural list); bind sf-database on `NODE_MESH_IP` instead of `DATABASE_NODE_IP` |
| [BYO MariaDB and sf-database tier](PLAN-byo-mariadb.md) | [Phase 3: gRPC tier](PLAN-byo-mariadb-phase-03-grpc-tier.md) | Complete | Multi-endpoint client-side gRPC LB; minimal `grpc.health.v1.Health` on sf-database; channel factory at `shakenfist/util/grpc_channel.py` |
| [BYO MariaDB and sf-database tier](PLAN-byo-mariadb.md) | [Phase 4: Deploy BYO](PLAN-byo-mariadb-phase-04-deploy-byo.md) | Complete | `getsf` prompts for connection details; `roles/mariadb/` deleted; `tools/bootstrap-mariadb.sql` ships; `deploy.py` stops generating a password; tuning `.cnf` moves to `examples/`; `SHAKENFIST_MARIADB_HOST=localhost` escape hatch dissolved |
| [BYO MariaDB and sf-database tier](PLAN-byo-mariadb.md) | [Phase 5: CI workflow step](PLAN-byo-mariadb-phase-05-ci-byo.md) | Complete | New tools/ci-install-mariadb.sh helper + five workflow sites in functional-tests.yml/scheduled-tests.yml install MariaDB and pass GETSF_MARIADB_* env vars to getsf-wrapper |
| [BYO MariaDB and sf-database tier](PLAN-byo-mariadb.md) | [Phase 6: CI tier coverage](PLAN-byo-mariadb-phase-06-ci-tier.md) | Complete | Multi-instance sf-database startup; `MARIADB_GATEWAY_HOSTS` rendered as a list; bind-all drop-in; `ci-topology-slim-tier` in `shakenfist/actions`; merge-queue matrix entry; functional LB-fanout test asserting each etcd_master saw at least 5% of traffic |
| [BYO MariaDB and sf-database tier](PLAN-byo-mariadb.md) | [Phase 7: Documentation](PLAN-byo-mariadb-phase-07-docs.md) | Complete | `docs/operator_guide/database.md` restructured to lead with BYO and stripped of historical etcd content; tier-model note added to `ARCHITECTURE.md`; deleted-skill bullet dropped from `README.md`; "Bring your own MariaDB" section appended to `docs/release_notes/v07-v08.md` |
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
| [Replace exec'd network commands with netlink](PLAN-replace-exec-with-netlink.md) | Phase 0: Research and decisions | Not started | Pick `pyroute2.nftables` vs `python-nftables`, handle sysctl / arping corners, scope the privexec split, pick auth model |
| [Replace exec'd network commands with netlink](PLAN-replace-exec-with-netlink.md) | Phase 1: rtnetlink for link / addr / route / neigh | Not started | Port `ip` exec sites to `pyroute2.IPRoute` |
| [Replace exec'd network commands with netlink](PLAN-replace-exec-with-netlink.md) | Phase 2: Bridge attributes via `IFLA_BR_*` | Not started | Replace `brctl` with rtnetlink bridge link attributes |
| [Replace exec'd network commands with netlink](PLAN-replace-exec-with-netlink.md) | Phase 3: nftables rules via netlink | Not started | Port iptables rules to nftables in atomic transactions |
| [Replace exec'd network commands with netlink](PLAN-replace-exec-with-netlink.md) | Phase 4: Stand up `sf-net-privexec` | Not started | New typed-API daemon holding `CAP_NET_ADMIN`, with `net-worker` as its only client |
| [Replace exec'd network commands with netlink](PLAN-replace-exec-with-netlink.md) | Phase 5: Shrink `sf-privexec` | Not started | Drop `CAP_NET_ADMIN` and network RPCs from the existing privexec daemon |
| [Replace exec'd network commands with netlink](PLAN-replace-exec-with-netlink.md) | Phase 6: Cleanup | Not started | Close out remaining `sf-net` direct-exec sites and the sysctl / arping corners |
| [Atomic scheduling via reservations](PLAN-scheduler-reservations.md) | Phase 0: Research and decisions | Not started | Resolve conditional-INSERT vs SELECT-FOR-UPDATE, reservation row schema, lifecycle states, affinity model rework, batch-create semantics, generic-vs-specific |
| [Atomic scheduling via reservations](PLAN-scheduler-reservations.md) | Phase 1: `node_reservations` schema | Not started | Schema and migration for the reservation table |
| [Atomic scheduling via reservations](PLAN-scheduler-reservations.md) | Phase 2: Conditional-INSERT primitive | Not started | The scheduling primitive that filters and claims atomically |
| [Atomic scheduling via reservations](PLAN-scheduler-reservations.md) | Phase 3: Reservation lifecycle | Not started | Consume on `building`, explicit release on failure, leased TTL reaper |
| [Atomic scheduling via reservations](PLAN-scheduler-reservations.md) | Phase 4: Migrate callers | Not started | Port the three in-process `Scheduler()` call sites |
| [Atomic scheduling via reservations](PLAN-scheduler-reservations.md) | Phase 5: Batch-create API | Not started | All-or-nothing multi-instance create primitive |
| [Atomic scheduling via reservations](PLAN-scheduler-reservations.md) | Phase 6: Affinity model rework | Not started | Implement the affinity decision from phase 0 |
| [Atomic scheduling via reservations](PLAN-scheduler-reservations.md) | Phase 7: Diagnostic-mode rejection logging | Not started | Restore per-rejection detail on failed schedules without paying the cost on every success |
| [Atomic scheduling via reservations](PLAN-scheduler-reservations.md) | Phase 8: Documentation | Not started | Operator guide for the new model and migration notes |
| [Remove the eventlog service](PLAN-eventlog-direct-mariadb.md) | [Phase 1: Schema, accessors, RPC, row-count gauge](PLAN-eventlog-direct-mariadb-phase-01-schema.md) | Complete | `events` / `event_objects` tables, `RecordEventBatch` on sf-database, `database_events_rows` gauge |
| [Remove the eventlog service](PLAN-eventlog-direct-mariadb.md) | [Phase 2: Write cut-over and metrics](PLAN-eventlog-direct-mariadb-phase-02-write.md) | Complete | Swap drainer's gRPC target to sf-database; promote `event_uuid` / `request_id`; wire spool-depth, drop, insert metrics |
| [Remove the eventlog service](PLAN-eventlog-direct-mariadb.md) | [Phase 3: Prune in cluster daemon](PLAN-eventlog-direct-mariadb-phase-03-prune.md) | Complete | Move per-event-type prune sweep into the cluster maintainer with multi-object semantics |
| [Remove the eventlog service](PLAN-eventlog-direct-mariadb.md) | [Phase 4: REST API direct-read](PLAN-eventlog-direct-mariadb-phase-04-read.md) | Complete | Event-list endpoints call `GetObjectEvents` on sf-database; no sqlite locality |
| [Remove the eventlog service](PLAN-eventlog-direct-mariadb.md) | [Phase 5: Delete the daemon](PLAN-eventlog-direct-mariadb-phase-05-remove.md) | Complete | Remove `sf-eventlog`, gRPC protos, systemd unit, config, `event_dlq`, and on-disk sqlite chunks |
| [Remove the eventlog service](PLAN-eventlog-direct-mariadb.md) | [Phase 6: Documentation](PLAN-eventlog-direct-mariadb-phase-06-docs.md) | Complete | Operator guide for new eventlog, history-loss called out in release notes, ARCHITECTURE/README/AGENTS |
| [Generic allocator](PLAN-generic-allocator.md) | Phase 0: Research and decisions | Not started | Pick allocation strategy, per-pool policy shape, leased-vs-permanent semantics, migration plan |
| [Generic allocator](PLAN-generic-allocator.md) | Phase 1: Schema and primitive | Not started | `resource_pool_allocations` table and conditional-INSERT allocator |
| [Generic allocator](PLAN-generic-allocator.md) | Phase 2: Port VXLAN allocator | Not started | First migration; sets the template |
| [Generic allocator](PLAN-generic-allocator.md) | Phase 3: Port console / VDI ports | Not started | Drop the local `socket.bind()` race-check |
| [Generic allocator](PLAN-generic-allocator.md) | Phase 4: Port vsock CID allocator | Not started | Drop the global cluster lock |
| [Generic allocator](PLAN-generic-allocator.md) | Phase 5: Port MAC allocator | Not started | Fix today's probabilistic-only correctness |
| [Generic allocator](PLAN-generic-allocator.md) | Phase 6: Documentation | Not started | Audit-log surface and developer docs |
| [Network service ports](PLAN-network-service-ports.md) | Phase 0: Research and decisions | Not started | Pick port range, TLS-on-shared-IP, token model, two-stage cleanup ordering, carrier-coupling contract |
| [Network service ports](PLAN-network-service-ports.md) | Phase 1: Pool registration | Not started | Register service-port pool with the generic allocator |
| [Network service ports](PLAN-network-service-ports.md) | Phase 2: API and token issuance | Not started | `allocate_service_port` / `release_service_port` |
| [Network service ports](PLAN-network-service-ports.md) | Phase 3: Carrier-side DNAT | Not started | Network daemon programs DNAT rules per allocation |
| [Network service ports](PLAN-network-service-ports.md) | Phase 4: Reaper and reconciler | Not started | Drift detection and repair across DB and iptables state |
| [Network service ports](PLAN-network-service-ports.md) | Phase 5: Validation surface | Not started | Smoke-test caller or first real caller |
| [Network service ports](PLAN-network-service-ports.md) | Phase 6: Documentation | Not started | Operator and developer docs including threat-surface change |
| [Network carrier model](PLAN-network-carrier-model.md) | Phase 0: Research and decisions | Not started | Resolve pool config, lease TTL, advertisement modes, DHCP state, VLAN-trunk forward compatibility |
| [Network carrier model](PLAN-network-carrier-model.md) | Phase 1: Carrier pool config | Not started | Eligible-carrier set and node-capability declaration |
| [Network carrier model](PLAN-network-carrier-model.md) | Phase 2: Per-network carrier lease | Not started | Lease primitive backed by `cluster_locks` and the generic allocator |
| [Network carrier model](PLAN-network-carrier-model.md) | Phase 3: Renderer process | Not started | Carrier-side process that materialises leased state to kernel |
| [Network carrier model](PLAN-network-carrier-model.md) | Phase 4: SNAT and floating IP | Not started | Render SNAT and egress IP via the carrier |
| [Network carrier model](PLAN-network-carrier-model.md) | Phase 5: Service ports via renderer | Not started | Carrier-side hookup for `PLAN-network-service-ports` |
| [Network carrier model](PLAN-network-carrier-model.md) | Phase 6: DHCP persistence | Not started | DHCP leases survive carrier change |
| [Network carrier model](PLAN-network-carrier-model.md) | Phase 7: DNS via renderer | Not started | DNS records rendered from data |
| [Network carrier model](PLAN-network-carrier-model.md) | Phase 8: BGP advertisement | Not started | Embedded or operator-external BGP speaker |
| [Network carrier model](PLAN-network-carrier-model.md) | Phase 9: L2 / GARP advertisement | Not started | Alternative for non-routed deployments |
| [Network carrier model](PLAN-network-carrier-model.md) | Phase 10: Migration | Not started | Cutover from singleton network node to smeared carriers |
| [Network carrier model](PLAN-network-carrier-model.md) | Phase 11: Operator documentation | Not started | VIP failover modes and pool sizing |
| [OIDC authentication](PLAN-oidc-authentication.md) | Master plan | Stub | OIDC as an authentication option for human users; existing namespace keys re-framed as service-account tokens for automation |

### Status Definitions

- **Stub**: Framing recorded for future detailed planning; not yet ready to execute
- **Not started**: Plan exists, work not yet begun
- **Planning**: Design complete, implementation not yet started
- **In Progress**: Currently being implemented
- **Complete**: Implemented and released
- **Future**: Planned but not yet designed in detail
- **Blocked on preconditions**: Plan exists but explicitly waits on another plan or external event before work can begin
