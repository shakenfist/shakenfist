# Development Plans

This section contains forward-looking roadmaps for Shaken Fist development. These documents describe planned features and architectural directions.

!!! warning "Forward-Looking Statements"
    Plans describe intended future work and may change based on implementation experience, community feedback, or shifting priorities. Check the status table below to see what has been implemented.

## Plan sequencing

The set of incomplete plans has grown to the point where the order they land in matters. The intended sequencing is:

1. **[Network operations facade](PLAN-network-facade.md)** — complete. Landed via the `network-facade` branch.
2. **Retire etcd** — _(absorbed into [BYO MariaDB and sf-database tier](PLAN-byo-mariadb.md) phase 0; the standalone `PLAN-remove-etcd.md` has been removed.)_ Inspection confirmed the etcd data drain is complete (the `DATA_MIGRATIONS` dict is empty), but the supporting machinery — `shakenfist/etcd.py`, `etcd3gw`, the etcd proto stubs, the drain test files, `is_etcd_master`, the migration-era `sf-ctl` aliases — is still in tree. Phase 0 of the BYO-MariaDB plan deletes it as a single sweep. The `etcd_master` ansible group name rename stays with `PLAN-remove-primary` phase 7 as a deploy-scope concern.
3. **[Health checks, readiness, and graceful drain](PLAN-health-checks.md)** — **complete.** A precondition for the BYO load-balancer story in remove-primary being operationally honest. Delivered sf-api `/livez`/`/readyz`/`/healthz` with SIGTERM drain, dependency-aware `grpc.health.v1` on sf-database, systemd `WATCHDOG` liveness on the worker/elected daemons (which also closes the cluster-lock proof-of-life gap), and operator LB/upgrade docs. Landed on the `health-checks` branch.
4. **[Remove the primary node](PLAN-remove-primary.md)** — the BYO-infrastructure scope reduction. Phase 7 finishes the deployer-level `etcd_master` → `database_node` rename, by which point the drain code itself is long gone. Naturally followed by a wipe-and-redeploy of Mikal's production cluster against the new shape.
5. **[BYO MariaDB and `sf-database` as a tier](PLAN-byo-mariadb.md)** — lifted out of remove-primary because it grew into its own master plan. Removes MariaDB-server install from the deployer entirely, reshapes `sf-database` into a deployer-chosen tier of equal stateless instances reached via client-side gRPC load balancing (not leader election), and carves schema/migration execution out of daemon startup into an operator-run `sf-ctl ensure-mariadb-schema` command. Can land in parallel with remove-primary's remaining phases; its phase 1 also performs the scope-shift edit to remove-primary itself.

[Remove syslog forwarding (ship logs to Loki)](PLAN-remove-syslog-forwarding.md) delivers the "Loki-shipper story" that remove-primary phase 1 is explicitly gated on — it adds structured-JSON logging and an in-process, on-disk-spooled Loki push (modelled on the eventlog spool/drainer) before deleting the rsyslog wiring. It can land in parallel with the other BYO work and is sequenced ahead of remove-primary phase 1, which it realises.

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
| [Health checks](PLAN-health-checks.md) | Phase 0: Research and decisions | Complete | Routing principle (LB probes only sf-api) collapsed OQ1/2/4/9; decided readiness cache, drain-grace/timeout reconciliation, WATCHDOG liveness + lock proof-of-life, auth, daemon classification |
| [Health checks](PLAN-health-checks.md) | Phase 1: sf-api endpoints and drain | Complete | `/livez`, `/readyz`, `/healthz` on sf-api; per-worker readiness checker; SIGTERM-driven drain (`API_DRAIN_GRACE`, reconciled timeouts) |
| [Health checks](PLAN-health-checks.md) | Phase 2: gRPC health on sf-database | Complete | `grpc.health.v1.Health` `Check` now tracks live MariaDB reachability via the daemon's ~10s loop; schema currency stays a startup refuse-to-start precondition; no Watch, no client `healthCheckConfig` |
| [Health checks](PLAN-health-checks.md) | Phase 3: WATCHDOG liveness wiring | Complete | Wired systemd `WATCHDOG` into the eight non-trivial daemons (`WatchdogSec=60s`, pet in `idle()` + cluster/cleaner heavy iterators); closes lock proof-of-life via watchdog-kill → lease-expiry failover |
| [Health checks](PLAN-health-checks.md) | Phase 4: Operator documentation | Complete | `load_balancing.md` HAProxy/nginx-FOSS/ALB probe configs; rolling-upgrade-with-drain in `upgrades.md`; live `ci_drain_check.sh` (in the actions repo) wired into functional-tests |
| [Remove the primary node](PLAN-remove-primary.md) | Phase 1: Remove monitoring | Not started | Drop rsyslog aggregation from the deployer (Grafana and the primary-node Prometheus server already removed as warmup) |
| [Remove the primary node](PLAN-remove-primary.md) | Phase 2: Bootstrap CLI | Not started | Idempotent `sf-ctl bootstrap-cluster` + `bootstrap_operations` table |
| [Remove the primary node](PLAN-remove-primary.md) | Phase 3: Remove LB | Complete (pending CI) | Realised by [Remove the Apache load balancer](PLAN-remove-apache-lb.md) |
| [Remove the primary node](PLAN-remove-primary.md) | Phases 4-5: _(moved to BYO MariaDB)_ | _(moved)_ | MariaDB BYO and `sf-database`-as-tier lifted into [PLAN-byo-mariadb.md](PLAN-byo-mariadb.md) |
| [Remove the primary node](PLAN-remove-primary.md) | Phase 6: Galaxy role | Not started | Repackage deployer as a per-node ansible-galaxy-style role |
| [Remove the primary node](PLAN-remove-primary.md) | Phase 7: Rename and cleanup | Not started | `etcd_master` → `database_node`; final dead-code sweep |
| [Remove the Apache load balancer](PLAN-remove-apache-lb.md) | [Phase 1: Document operator-provided LB](PLAN-remove-apache-lb-phase-01-docs.md) | Complete | Example apache2 + nginx configs and the `localhost:13000` single-node escape hatch (realises remove-primary phase 3) |
| [Remove the Apache load balancer](PLAN-remove-apache-lb.md) | [Phase 2: Remove Apache from the deployer](PLAN-remove-apache-lb-phase-02-remove.md) | Complete (pending CI) | Delete `apache2.yml` + `apache-site-primary.conf`; repoint single-node `api_url` to `:13000` |
| [Remove syslog forwarding (ship logs to Loki)](PLAN-remove-syslog-forwarding.md) | [Phase 0: Decisions and design](PLAN-remove-syslog-forwarding-phase-00-decisions.md) | Complete | Config surface, Loki label/field-name contract, spool factoring, auth, CI Loki topology — answers recorded in the master plan's Decisions section |
| [Remove syslog forwarding (ship logs to Loki)](PLAN-remove-syslog-forwarding.md) | [Phase 1: Default structured JSON logging](PLAN-remove-syslog-forwarding-phase-01-json-logging.md) | Complete | `shakenfist-utilities==0.8.5` (JSON-only daemon logging, clean record, field contract, tests, draft superseded) released to PyPI and pinned in shakenfist |
| [Remove syslog forwarding (ship logs to Loki)](PLAN-remove-syslog-forwarding.md) | [Phase 2: Loki shipper in shakenfist](PLAN-remove-syslog-forwarding-phase-02-loki-shipper.md) | Complete (pending CI) | `logship_spool.py` / `logship_drainer.py` + a `logging.Handler` modelled on the eventlog spool/drainer; HTTP push, `LOKI_*` config, lifecycle wiring, metrics, `LOG_EVENTS_TO_LOKI` echo guard, mypy-covered, tests |
| [Remove syslog forwarding (ship logs to Loki)](PLAN-remove-syslog-forwarding.md) | [Phase 3: CI Loki](PLAN-remove-syslog-forwarding-phase-03-ci-loki.md) | Not started | Stand up Loki for the functional cluster (as MariaDB is) via `tools/ci-install-loki.sh`, plumb `LOKI_BASE_URL` through getsf/deploy into `/etc/sf/config`, and a functional test asserting SF logs reach Loki end-to-end |
| [Remove syslog forwarding (ship logs to Loki)](PLAN-remove-syslog-forwarding.md) | [Phase 4: Rework CI tooling](PLAN-remove-syslog-forwarding-phase-04-ci-tooling.md) | Not started | New versioned `shakenfist/actions` log-checks querying Loki (structured `\| json` LogQL) + clingwrap bundle dumping Loki and per-node local journald (so a shipper failure stays debuggable) |
| [Remove syslog forwarding (ship logs to Loki)](PLAN-remove-syslog-forwarding.md) | Phase 5: Remove rsyslog forwarding | Not started | Delete rsyslog client/server configs, `syslog_target`, the rsyslog install, and gunicorn `--log-syslog`; switch CI to the Loki-aware checks (realises remove-primary phase 1) |
| [Remove syslog forwarding (ship logs to Loki)](PLAN-remove-syslog-forwarding.md) | Phase 6: Documentation | Not started | `docs/operator_guide/logging.md` (BYO-Loki contract), ARCHITECTURE/README/AGENTS, index/order |
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
| [Artifact UX rework](PLAN-artifact-ux-rework.md) | Master plan | Stub | Rework the upload/blob/artifact/label/snapshot surface to remove usability sharp edges (ambiguous name resolution, blob-UUID juggling, underpowered labels, instance-costumed snapshots); adopts #3271, #1634, #1167, #592, #877, #1386, #833, #422 |

### Status Definitions

- **Stub**: Framing recorded for future detailed planning; not yet ready to execute
- **Not started**: Plan exists, work not yet begun
- **Planning**: Design complete, implementation not yet started
- **In Progress**: Currently being implemented
- **Complete**: Implemented and released
- **Future**: Planned but not yet designed in detail
- **Blocked on preconditions**: Plan exists but explicitly waits on another plan or external event before work can begin
