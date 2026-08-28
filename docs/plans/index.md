# Development Plans

This section contains forward-looking roadmaps for Shaken Fist development. These documents describe planned features and architectural directions.

!!! warning "Forward-Looking Statements"
    Plans describe intended future work and may change based on implementation experience, community feedback, or shifting priorities. Check the status table below to see what has been implemented.

## Plan sequencing

The set of incomplete plans has grown to the point where the order they land in matters. The intended sequencing is:

1. **[Network operations facade](PLAN-network-facade.md)** — complete. Landed via the `network-facade` branch.
2. **Retire etcd** — _(absorbed into [BYO MariaDB and sf-database tier](PLAN-byo-mariadb.md) phase 0; the standalone `PLAN-remove-etcd.md` has been removed.)_ Done. The data drain was already complete (the `DATA_MIGRATIONS` dict is empty), and the supporting machinery — `shakenfist/etcd.py`, `etcd3gw`, the etcd proto stubs, the drain test files, the migration-era `sf-ctl` aliases — was deleted as a single sweep by the BYO-MariaDB plan. The `etcd_master` ansible group rename landed with `PLAN-remove-primary`. What survives is deliberate: the vestigial `is_etcd_master` flag is pinned False for one release as a rollback fallback.
3. **[Health checks, readiness, and graceful drain](PLAN-health-checks.md)** — **complete.** A precondition for the BYO load-balancer story in remove-primary being operationally honest. Delivered sf-api `/livez`/`/readyz`/`/healthz` with SIGTERM drain, dependency-aware `grpc.health.v1` on sf-database, systemd `WATCHDOG` liveness on the worker/elected daemons (which also closes the cluster-lock proof-of-life gap), and operator LB/upgrade docs. Landed on the `health-checks` branch. **[Node resource health](PLAN-node-resource-health.md)** is a *sibling* (complete) on a different axis — it drives `node.state` from the health of the storage/resource dependencies a node's hosted object types declare, so a dead disk or hung NFS mount takes a node out of scheduling. It was not sequenced against the BYO thread; it grew out of the sf-6 blob-NVMe incident and landed independently on the `node-resource-health` branch.
4. **[Remove the primary node](PLAN-remove-primary.md)** — the BYO-infrastructure scope reduction. Complete: the deployer-level `etcd_master` → `database_node` rename landed with one-release fallbacks, and the deployer is now the `shakenfist.shakenfist` collection. Naturally followed by a wipe-and-redeploy of Mikal's production cluster against the new shape.
5. **[BYO MariaDB and `sf-database` as a tier](PLAN-byo-mariadb.md)** — lifted out of remove-primary because it grew into its own master plan. Removes MariaDB-server install from the deployer entirely, reshapes `sf-database` into a deployer-chosen tier of equal stateless instances reached via client-side gRPC load balancing (not leader election), and carves schema/migration execution out of daemon startup into an operator-run `sf-ctl ensure-mariadb-schema` command. Complete. It landed in parallel with remove-primary's remaining phases, and its first phase performed the scope-shift edit to remove-primary itself.

[Remove syslog forwarding (ship logs to Loki)](PLAN-remove-syslog-forwarding.md) delivers the "Loki-shipper story" that remove-primary phase 1 is explicitly gated on — it adds structured-JSON logging and an in-process, on-disk-spooled Loki push (modelled on the eventlog spool/drainer) before deleting the rsyslog wiring. It can land in parallel with the other BYO work and is sequenced ahead of remove-primary phase 1, which it realises.

The remaining incomplete plans — [Embrace TLS](PLAN-embrace-tls.md), [Sticky blob transfers](PLAN-sticky-transfers.md), [Replace exec'd network commands with netlink](PLAN-replace-exec-with-netlink.md), [Atomic scheduling via reservations](PLAN-scheduler-reservations.md), the connected [Generic allocator](PLAN-generic-allocator.md) / [Network service ports](PLAN-network-service-ports.md) / [Network carrier model](PLAN-network-carrier-model.md) triple, and the not-yet-drafted OpenTelemetry instrumentation thread — are intentionally **not ordered relative to each other** here. They each have specific dependencies on either remove-primary having established the BYO shape (the operator-provides-PKI surface for TLS, the streaming-proxy baseline for sticky transfers, the sf-database election pattern for the others) or network-facade having landed (the netlink plan, whose privilege-separation phases need network-facade's single-mutator property), but among themselves the order is a triage decision still to be made now that remove-primary has landed. The scheduler-reservations plan is independent of the BYO shape but benefits from the OpenTelemetry thread landing first so that phase 0's design choices can be informed by real load and contention numbers.

[Database load reduction](PLAN-database-load-reduction.md) sits outside that triage: it addresses a measured production problem (the sf-database tier serving ~527 ops/second at idle, 57% of it one polling loop). Phases 1–6 are complete and took that to ~102 ops/second on a quiet cluster, largely by removing fixed-rate idle polling and restoring the etcd-era “objects are cacheable, attributes of objects are not” principle in a MariaDB form. Its phase 4 (caller attribution via gRPC metadata and a per-caller counter) is also the first concrete slice of the OpenTelemetry instrumentation thread — the caller-identity plumbing is what a later span-propagation phase would reuse, it is designed to compose with the mTLS peer-identity model from [Embrace TLS](PLAN-embrace-tls.md) rather than duplicate it, and it is what made every subsequent diagnosis possible. Phase 6 chased what looked like a rot back to ~142 ops/second and found that most of it was the counter learning to see two more nodes and the cluster daemon (#3708) rather than new load — so the earlier “target met” reading is withdrawn, though the hunt still turned up two real defects worth ~21 ops/second (#3814, #3655). Its durable output is a load *model* with a per-node term, and phase 7 makes regression detection against that model something any deployer can run rather than something only our own operations tooling can see.

The **generic-allocator / network-service-ports / network-carrier-model triple** is internally ordered. Generic-allocator is the foundational refactor (replaces five ad-hoc allocators with one primitive and is independently shippable). Network-service-ports builds on the allocator to expose per-network DNAT'd ports for managed services (web consoles, transfer agents, managed VPN endpoints). Network-carrier-model layers a smeared lease-based per-network carrier role with VIP advertisement on top, removing the network-node singleton; it depends on both prior plans and is the largest of the three. The triple supersedes the "network node failover" thread that was previously a not-yet-drafted line item.

[Declarative API input validation](PLAN-api-input-validation.md) is independent of all of the above and gated on nothing, so it can start whenever there is appetite. It closes a cluster of reported issues going back to 2020 (#528 and #936 are the parent issues) whose common cause is that request body values reach handlers untyped. It is drafted now because a per-endpoint fix for one instance of it (#3609) was attempted and abandoned: seven hand-rolled guards, two of them wrong on the first attempt, and the defect class still not covered. Its one coordination point is with the [API query batching roadmap](api-query-batching-roadmap.md), which needs the same bounded `limit`/`offset` parameter types for #1974.

[Agent operation deadlines and progress detection](PLAN-agent-operation-deadlines.md) is similarly independent and gated on nothing. It is driven by CI reliability, but the defect is user-facing too: the #3516 `sf-sidechannel` wedge (a `get-file` stuck in `executing` until the client times out, with the agent-side trigger tracked as #2240) is one of the two most frequent merge-queue flakes as of 2026-08, and because each instance runs at most one executor, a wedged operation also blocks every other agent operation against that instance until the 900-second backstop fires. It adds client-propagated wall-clock deadlines and per-command progress timeouts to agent operations, plus a retry path for operations that fail in `executing`. Its API-facing phase follows the parameter declaration rules that [Declarative API input validation](PLAN-api-input-validation.md) enforces, but does not depend on that plan's remaining phases. Its follow-on, [Dependency-aware agent operations](PLAN-agent-operation-dependencies.md), extends the cluster operation `depends_on`/`runs_after` vocabulary to agent operations (including cross-instance edges, enabling fire-and-await fleet orchestration such as rolling updates) and elevates documentation-with-worked-examples to a deliverable, with `client-python-k3s` as the first of an example-application suite; it is explicitly gated on the deadlines plan landing, because dependency waiting relies on deadline expiry to break user-created cycles.

[Bound the size of DatabaseService gRPC replies](PLAN-grpc-bounded-replies.md) is independent and gated on nothing, but it is driven by a production defect rather than an ambition, so it should not sit indefinitely. Several `DatabaseService` replies are unbounded by construction and the only ceiling on them is gRPC's message size limit, which the sfcbr cluster has now crossed twice from two daemons on two RPCs (#3638). A stopgap has raised the client cap and made the affected callers honest about failed reads — including one that was using an unreadable list as a licence to delete a node's blob store — but the protocol still permits replies whose size we discover by having one fail. Its phase 2 (sweep callers ask for a bounded page rather than everything) is small and independently shippable; its phase 3 is a protocol change and inherits the eventlog plan's deferred cursor-pagination work.

[Right-size the CI test clouds](PLAN-ci-cloud-sizing.md) is independent of all of the above and gated on nothing. It is driven by measurement rather than ambition: the five-hypervisor test cloud each CI job builds was an arbitrary choice that had never been checked, and checking it found that what binds is the scheduler's admission ledger (a 4 vCPU node yields 6 admitted vCPU, a 4 vCPU network node yields 3) rather than real CPU or memory, which sit at roughly 18% and 50% of allocation. The three-node `slim-tier` topology is below that line and passes 19% of merge runs, which is where the #3772 507 family comes from. The plan instruments headroom first, converts the coverage we currently get from scarcity by accident into explicit tests, and only then reshapes the topologies -- deliberately, because a bigger cloud would otherwise close #3772 and #3565 by silencing them.

The blob-storage and SQL-pushdown roadmaps and the network-facade plan run on their own cadence and are not part of this sequencing. Neither are the plans which appear only in the table below: [sf-netserv](PLAN-netserv.md), [CI node-exec assertions](PLAN-ci-node-exec-assertions.md), [attribute field masks](PLAN-attribute-field-masks.md), [retry transient artifact fetches](PLAN-artifact-fetch-retry-with-backoff.md), [fix cluster_operation_targets uniqueness](PLAN-fix-cluster-operation-targets-unique-constraint.md) and [Kerbside VDI console tokens](PLAN-kerbside-vdi-tokens.md) — the first is a thoughtbubble and the rest are self-contained pieces of work that were never sequenced against this thread.

## Status vocabulary

The `Status` column below holds exactly one of these terms and nothing else. It is the
whole-plan status, so it only reads `Complete` once every phase has landed. Where a plan
is `In progress` or `Blocked` for a reason the `Phases` column does not make obvious, that
reason belongs in the plan itself rather than in this table. The canonical wording is the
`plan-status-vocabulary` shared block in
[PLAN-TEMPLATE.md](https://github.com/shakenfist/shakenfist/blob/develop/PLAN-TEMPLATE.md).

| Status | Meaning |
|--------|---------|
| Proposed | Written down as a concept, not yet scheduled. |
| Not started | Scheduled, but no work has begun. |
| In progress | Work has begun and has not finished. |
| Blocked | Cannot proceed until something outside the plan changes. |
| Complete | The work is done. |
| Abandoned | Deliberately dropped without being done. |
| Superseded | Replaced by another plan, which the plan names. |

## Master plans

Most master plans track their own phases -- a phase plan and a status for each -- in their
own Execution table, and the `Phases` column here is arithmetic over that table; follow the
plan link for what each phase covers, and for why a plan is where it is. It counts
*completed* phases, so a plan whose phases were abandoned or superseded rather than done can
read `Complete` without the two numbers meeting, and `—` means the plan has no enumerated
phases yet, or has a table which is still a placeholder rather than a phase list.

Three plans are counted by hand because they do not keep a phase table:
[blob storage](blob-storage-roadmap.md), [API query
batching](api-query-batching-roadmap.md) and [attribute field
masks](PLAN-attribute-field-masks.md) carry their phases as headings.
Two more publish no arithmetic because their tables are placeholders: [owning more of the
QEMU stack](PLAN-qemu-futures.md) names three phases and then an ellipsis, and [artifact UX
rework](PLAN-artifact-ux-rework.md) a decisions pass and "(later phases)". These five are the
checker's blind spot, and the only numbers here it cannot recompute.

| Date | Plan | Intent | Status | Phases |
|------|------|--------|--------|--------|
| 2026-01-08 | [Blob storage roadmap](blob-storage-roadmap.md) | Composite blobs, lazy deduplication and content-defined chunking | In progress | 1 of 4 |
| 2026-01-10 | [API query batching](api-query-batching-roadmap.md) | Batch and prefetch related objects so list endpoints stop issuing per-object queries | Not started | 0 of 4 |
| 2026-04-23 | [SQL-pushdown filtering](PLAN-sql-pushdown-filtering.md) | Push object filtering into MariaDB instead of scanning and filtering in Python | Complete | 7 of 7 |
| 2026-05-06 | [Replace `last_cluster_operation`](PLAN-replace-last-cluster-operation.md) | Trade the single-pointer gate for an append-only `cluster_operation_targets` history | Complete | 5 of 5 |
| 2026-05-14 | [Fix `cluster_operation_targets` uniqueness](PLAN-fix-cluster-operation-targets-unique-constraint.md) | Composite UNIQUE so a multi-target operation records all of its target rows | Complete | — |
| 2026-05-15 | [Network operations facade](PLAN-network-facade.md) | Split `Network` into a queue-enqueuing facade and a single-mutator worker | Complete | 10 of 10 |
| 2026-05-16 | [Embrace TLS](PLAN-embrace-tls.md) | Operator-provided PKI and TLS on every internal and external listener | Not started | 0 of 9 |
| 2026-05-16 | [Recurring cluster operations](PLAN-recurring-operations.md) | A cron-like framework absorbing the scheduled-task loops, plus user-facing recurrence | Proposed | 0 of 8 |
| 2026-05-16 | [Remove the primary node](PLAN-remove-primary.md) | Retire the special primary node in favour of a BYO-infrastructure deployer | Complete | 5 of 7 |
| 2026-05-16 | [Sticky blob transfers](PLAN-sticky-transfers.md) | Session affinity for blob transfers, deferred until OpenTelemetry supplies upload-path numbers | Not started | 0 of 6 |
| 2026-05-17 | [Retry transient artifact fetches](PLAN-artifact-fetch-retry-with-backoff.md) | A per-operation retry budget, so a network blip during a fetch no longer errors the artifact | Complete | — |
| 2026-05-17 | [Health checks, readiness and drain](PLAN-health-checks.md) | `/livez`, `/readyz`, `/healthz`, gRPC health, watchdog liveness and SIGTERM drain | Complete | 5 of 5 |
| 2026-05-20 | [Replace exec'd network commands with netlink](PLAN-replace-exec-with-netlink.md) | Native netlink calls in place of shelling out to `ip`, `bridge` and friends | Not started | 0 of 8 |
| 2026-05-22 | [Eventlog direct to MariaDB](PLAN-eventlog-direct-mariadb.md) | Remove the eventlog service and write events straight to MariaDB | Complete | 7 of 7 |
| 2026-05-22 | [Generic allocator](PLAN-generic-allocator.md) | Replace five ad-hoc finite-resource allocators with a single primitive | Not started | 0 of 8 |
| 2026-05-22 | [Network carrier model](PLAN-network-carrier-model.md) | A lease-based per-network carrier with VIP advertisement, retiring the network-node singleton | Not started | 0 of 13 |
| 2026-05-22 | [Network service ports](PLAN-network-service-ports.md) | Per-network DNAT'd ports for managed services | Not started | 0 of 8 |
| 2026-05-22 | [Atomic scheduling via reservations](PLAN-scheduler-reservations.md) | Guarded-UPDATE capacity counters and namespace claims in place of read-then-place scheduling | In progress | 8 of 14 |
| 2026-05-24 | [Queue performance and coalescing](PLAN-queue-performance.md) | Batched dequeue and cluster-operation coalescing. The wait tail is gone and explicit fairness was not needed, though those numbers were measured while coalescing was inert -- a push audit found it had never worked (#3878), and review of that fix found it would have folded per-node mesh ops across nodes (#3884). Reopened for three phases: proving coalescing works on a running cluster and measuring what it costs, the flat 15 second dependency wait (#3863), and a multi-column fold key (#3884). Phase 9 has now measured `sfcbr` for 42 hours: the fold costs a 3.7 ms median rather than the ~200 ms the code asserted, and it matched 7 times in 1,335 attempts -- so coalescing is confirmed working and confirmed nearly inert on this workload. Phase 10's subject moved when #3916 fixed #3863, but the same window shows deferral still matters, so it needs re-scoping rather than closing | In progress | 8 of 11 |
| 2026-06-01 | [OIDC authentication](PLAN-oidc-authentication.md) | Federated login against an external OIDC provider | Not started | 0 of 11 |
| 2026-06-02 | [Owning more of the QEMU stack](PLAN-qemu-futures.md) | Direct QMP control, and perhaps one day libvirt's job as well | Not started | — |
| 2026-06-03 | [BYO MariaDB and `sf-database` as a tier](PLAN-byo-mariadb.md) | A deployer-chosen database tier reached by client-side gRPC load balancing | Complete | 8 of 8 |
| 2026-06-12 | [Remove the Apache load balancer](PLAN-remove-apache-lb.md) | An operator-provided load balancer in place of the bundled Apache | Complete | 2 of 2 |
| 2026-06-13 | [Artifact UX rework](PLAN-artifact-ux-rework.md) | Rework the artifact, blob, label, upload and snapshot user interface | Proposed | — |
| 2026-06-19 | [Remove syslog forwarding](PLAN-remove-syslog-forwarding.md) | Structured JSON logging shipped to Loki, replacing the rsyslog wiring | Complete | 7 of 7 |
| 2026-07-13 | [CI node-exec assertions](PLAN-ci-node-exec-assertions.md) | Run an assertion on a named cluster node, and the floating-IP and network lifecycle tests that use it | Complete | — |
| 2026-07-14 | [Workload identity federation](PLAN-auth-federation.md) | Federated workload identity and first-class namespace keys | Complete | 7 of 7 |
| 2026-07-17 | [Attribute field masks everywhere](PLAN-attribute-field-masks.md) | Masked attribute writes, and the node instances list un-packed into `object_references` | Complete | 2 of 2 |
| 2026-07-19 | [Truthful cluster operation visibility](PLAN-cluster-op-visibility.md) | An observational flag, so "is anything in flight?" stops counting background housekeeping | In progress | 2 of 7 |
| 2026-07-19 | [Database load reduction](PLAN-database-load-reduction.md) | Cut steady-state MariaDB load from the `sf-database` tier, and keep it cut | In progress | 6 of 8 |
| 2026-07-19 | [Kerbside VDI console tokens](PLAN-kerbside-vdi-tokens.md) | Cluster-signed tokens exchanged for a VDI console session, across four repositories | In progress | 9 of 11 |
| 2026-07-19 | [Node resource health](PLAN-node-resource-health.md) | Declarative dependency checks driving node state, so a dead disk stops scheduling | Complete | 5 of 5 |
| 2026-07-21 | [Per-host resource reservations](PLAN-per-host-resource-reservations.md) | Per-node RAM, CPU and disk reservation overrides | Complete | 4 of 4 |
| 2026-07-28 | [sf-netserv](PLAN-netserv.md) | Replace dnsmasq with a Rust per-network service plane | Proposed | — |
| 2026-08-03 | [API input validation](PLAN-api-input-validation.md) | Declarative request validation and a consistent error contract for the REST API | In progress | 3 of 8 |
| 2026-08-14 | [Agent operation deadlines](PLAN-agent-operation-deadlines.md) | Client-propagated deadlines and per-command progress timeouts for agent operations | In progress | 5 of 9 |
| 2026-08-14 | [Dependency-aware agent operations](PLAN-agent-operation-dependencies.md) | `depends_on` and `runs_after` for agent operations, including cross-instance edges | Blocked | — |
| 2026-08-16 | [Bound gRPC reply sizes](PLAN-grpc-bounded-replies.md) | Make `DatabaseService` replies bounded by construction rather than by the message size limit | Not started | 0 of 7 |
| 2026-08-27 | [Right-size the CI test clouds](PLAN-ci-cloud-sizing.md) | Measured sizing for the nested CI clouds, with headroom instrumentation and explicit saturation coverage before any cloud grows | In progress | 1 of 7 |
