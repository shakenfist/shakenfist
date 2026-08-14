# Subsystem internals

Developer-facing notes on how particular subsystems behave, complementing
the operator-facing pages they link to.

## Scheduler and node capacity metrics

Atomic reservation-table scheduling is being built per
[PLAN-scheduler-reservations](../plans/PLAN-scheduler-reservations.md);
the capacity tables and their reconciler exist but are inert, and the
notes below describe both what runs today and the constraints on
switching admission over to them.

The scheduler ranks hypervisors by load per schedulable thread and
admits against reservation-adjusted capacity. The reservation
arithmetic lives in `shakenfist/daemons/resources/main.py`
(`_compute_reservations()`, `_get_hybrid_core_counts()`), which
publishes `cpu_cores`, `cpu_threads`, `cpu_cores_reserved`,
`cpu_schedulable`, `cpu_cores_schedulable`, `memory_reserved_mb`,
`disk_reservation_gb` (and `cpu_cores_performance` /
`cpu_cores_efficiency` on hybrid CPUs) into `node_metrics`. On the
consuming side, `Scheduler._schedulable_threads()` and
`Scheduler._memory_reserved_mb()` in `shakenfist/scheduler.py`
apply per-node fallbacks for metrics rows written by older
resources daemons (the CPU fallback subtracts this node's own
`NODE_CPU_RESERVATION_THREADS`, with no infra-role bump, so
un-upgraded nodes don't look artificially large) — admission,
ordering and `summarize_resources()` all go through these helpers,
so keep them in sync if you touch capacity arithmetic.
A third participant mirrors the same arithmetic: the scheduler
capacity reconciler's limit-derivation helpers in
`shakenfist/mariadb.py` (`_derive_cpu_memory_limits()`,
`_derive_disk_limit_gb()`) deliberately reproduce `scheduler.py`'s
admission *limits*, so a counter-based guard would bound capacity the
same way today's Python filter does — a change to one must change
both. The mirroring covers the arithmetic but not the inputs:
`_schedulable_threads()` and `_memory_reserved_mb()` substitute
per-node fallbacks for a metrics row that predates the typed capacity
columns, where the reconciler derives None and prefers to write no
capacity row at all rather than a guessed one. So mid-upgrade a node
can be schedulable while having no capacity row — the conservative
direction, but the two are not interchangeable while an upgrade is in
flight. The *usage* side is deliberately not a mirror: the reconciler's
`used_cpus` and `used_memory_mb` are allocation ledgers over every
placed, non-deleted instance, whereas the resources daemon's
`cpu_total_instance_vcpus` and `memory_total_instance_actual` count
only active libvirt domains. A powered-off instance holds its
reservation in the ledger and is absent from the measurement, so the
two legitimately disagree, and any counter-based admission has to
choose between them explicitly rather than assume parity. Today's CPU admission has
already made that choice for itself, without the reconciler's tables:
`Scheduler._committed_vcpus()` walks each candidate's
`INSTANCE_LOCATION` rows and charges the node `max(measured,
committed)`, because the measurement cannot see an instance which has
been placed but has not booted (issue 3498). That walk applies the
same two exclusions as `_RECONCILE_USAGE_SQL` -- skip deleted
instances, and count an instance only against the node its own
placement attribute names -- so the Python and SQL ledgers cannot
disagree about what "placed" means. Neither exclusion is served by the
static object cache (states and attributes are excluded from it), so
they are applied only on demand: the first pass sums cached static
values into an *upper bound*, and since an exclusion can only lower
the charge, only a node that bound would be rejected pays to find out
whether the reason is real. It is a stopgap for the CPU stage
only. When the guarded UPDATE against `scheduler_node_capacity` lands
it **replaces** this, and must delete `_committed_vcpus()` in the same
change rather than layer the counters on top of it, or admission ends
up consulting two ledgers which can drift apart. The
ledger also reads only
`INSTANCE_LOCATION` rows in `object_references`: during the one
transition release where `Node.instances` still unions in the legacy
`node_attributes.instances` JSON column, a placement written by a
pre-cutover node exists only in that column and is invisible to the
ledger, so mid-rolling-upgrade the `used_*` counters under-count — the
non-conservative direction for an admission guard, so the counter guard
must not be enabled until the legacy column and its union are
removed. The reconciler maintains the
`scheduler_node_capacity`, `namespace_claims` and `cluster_capacity`
tables from the elected cluster node every five minutes. Rows exist
per *schedulable hypervisor*, not per node, because a row that
describes capacity the scheduler would never use is worse than no row
at all: it inflates the cluster totals. Four filters enforce that,
each mirroring something `scheduler.py` already does — the
`is_hypervisor` column projected into `node_metrics` (sf-resources
publishes metrics from every node whatever its roles), node state
against `constants.NODE_ACTIVE_STATES` (the scheduler builds its
candidates from `Nodes([], prefilter='active')`; the filter is
expressed positively, as membership in the active set, so a node with
no state row at all is excluded exactly as `get_objects_by_state`
would exclude it), metrics freshness against
`RECONCILE_METRICS_MAX_AGE_SECONDS` (the scheduler discards metrics
older than 120s; the reconciler's window is much wider because its
cadence is), and existence in the `nodes` table. The
`cluster_capacity` singleton is a closed accounting over the nodes
that pass those filters: usage on a node without a capacity row counts
toward neither the total nor the unclaimed-used side (a claim's
`used_*` stays namespace-wide — a quota covers a namespace's instances
wherever they are stranded). If you add a capacity consumer, it
inherits these filters by reading the tables — do not re-derive
capacity from `node_metrics` directly. In this release nothing
consumes the tables for admission
(`docs/plans/PLAN-scheduler-reservations-phase-02-capacity-tables.md`).
The SQL itself is covered by
`shakenfist/tests/test_mariadb_capacity_reconcile_live.py`, which runs
against a real MariaDB in the "Schema ENUM widening" CI job (whose
script runs every `test_mariadb_*_live` module behind one MariaDB
install); the mocked unit tests cannot catch a broken uuid join, a
JSON_TABLE change, or an enum binding that names the wrong storage
convention, because all of those fail as silently wrong numbers rather
than errors. The live suite runs under `utf8mb4_bin` for that last
reason: `object_states.object_type` is a native `sa.Enum` persisting
member *names* while the `object_references` type and relationship
columns store member *values* (written as `str(member)`), and only a
case-sensitive collation makes a binding that confuses the two fail.
Operator-facing documentation is
[`scheduler.md`](../operator_guide/scheduler.md).


## VDI console token mint path

Shaken Fist mints short lived Ed25519 JWTs for the Kerbside VDI console
proxy. `shakenfist/external_api/instance.py`
(`InstanceVDIProxyConsoleHelperEndpoint`, `GET
/instances/<ref>/vdiconsoleproxy`) mints a token and returns a proxy URL;
`shakenfist/util/vdi_tokens.py` owns all key handling (mint, ensure, rotate,
public view); `shakenfist/external_api/admin.py`
(`AdminVDITokenPublicKeyEndpoint`, `GET /admin/vditokenpubkey`) publishes the
public verification keys. The signing key lives in a single `cluster_config`
row, `KERBSIDE_JWT_SIGNING_KEY` (two-key rotation window). The `sf-ctl`
`ensure-kerbside-signing-key` / `rotate-kerbside-signing-key` subcommands
bootstrap and rotate it. Operator runbook:
`docs/operator_guide/vdi_console_tokens.md`.


## Object References

The `object_references` table in MariaDB tracks relationships between objects.
This is used primarily for blob reference counting but is generic enough to
track any object-to-object relationship.

| Column | Type | Description |
|--------|------|-------------|
| source_object_type | ObjectType | Type of the referencing object |
| source_uuid | UUID | UUID of the referencing object |
| relationship | RelationshipType | Type of relationship |
| relationship_value | VARCHAR(64) | Optional relationship-specific value |
| target_object_type | ObjectType | Type of the referenced object |
| target_uuid | UUID | UUID of the referenced object |
| created | FLOAT | When the reference was created |
| last_active | FLOAT | Last time the reference was verified |

### Relationship Types

| Type | Source | Target | Value |
|------|--------|--------|-------|
| `disk` | Instance | Blob | Disk index ("0", "1", ...) |
| `nvram_template` | Instance | Blob | NULL |
| `artifact_index` | Artifact | Blob | Index number ("000000000001") |
| `depends_on` | Blob | Blob | NULL |
| `transcode` | Blob | Blob | Style ("qcow2", "raw") |
| `agent_output` | AgentOperation | Blob | Output type ("stdout", "stderr") |
| `blob_location` | Node | Blob | NULL (blob is present on this node) |
| `instance_location` | Node | Instance | NULL (instance is placed on this node) |

This replaces the legacy `ref_count` and `locations` blob attributes with a
queryable, auditable reference system. Blob reference counts are computed
dynamically from this table via `mariadb.count_references_to()`. Blob locations
are queried via `mariadb.get_references_to()` filtered by `BLOB_LOCATION`.

`INSTANCE_LOCATION` references similarly replace the legacy `instances`
JSON list on `node_attributes`: the list was maintained by read-modify-write
of the whole attributes row, so concurrent full-row writers (for example the
sentinels' 15-second `observe_this_node()` heartbeat) could silently revert
a placement. References are single-row inserts and deletes, needing no
cross-writer coordination. `Node.instances` queries them via
`mariadb.get_references_from()` filtered by `INSTANCE_LOCATION`. Unlike
`BLOB_LOCATION`, these rows key the node by UUID, not FQDN. For one
transition release the legacy column is dual-written (masked, under the
`instances` lock) and unioned into `Node.instances` reads, so placements
written by not-yet-upgraded nodes mid-roll stay visible and a rollback
still reads fresh data; each node's queues-daemon startup reconciliation
converges the two stores, and the column is dropped next release.

The `last_active` column is updated whenever a reference is observed to still
be valid (e.g., when a node's cleaner daemon calls `observe()` on local blobs).
This enables detection of stale references for cleanup.


## REST API surface

**202+poll contract for delete endpoints.** `DELETE /networks/<uuid>` and
`DELETE /networks` return HTTP 202 (Accepted). The response body carries
the cluster-operation handle so clients can poll for completion:

- Single delete: `{'op_type': 'net_op', 'op_uuid': '<uuid>'}`.
- Bulk delete: a list of `{'network_uuid': '...', 'op_type': 'net_op',
  'op_uuid': '...'}` entries, one per network.

**Cluster-operation discovery endpoints.** Two endpoints under
`/clusteroperations/` allow callers to inspect op history:

- `GET /clusteroperations/<op_uuid>/chain` — walks the `depends_on` graph
  from `<op_uuid>` and returns the full transitive ancestor closure as a
  list of op-summary dicts. Namespace-scoped: admin callers see everything;
  non-admin callers receive HTTP 403 if any chain member belongs to a
  foreign namespace. The op uuid is sufficient (no `<op_type>` segment)
  because op uuids are globally unique.
- `GET /clusteroperations?target_object_type=<type>&target_uuid=<uuid>` —
  returns all ops that targeted the given object. Namespace filtering is
  applied at the SQL layer (via a JOIN on `cluster_operation_targets`
  against namespace-carrying static-values tables) so large result sets
  are never materialised in Python.

**`redirect_to_network_node` status.** The `@redirect_to_network_node`
decorator (which proxies HTTP requests from the receiving API server to the
network node's gunicorn) has been removed from three of its four historical
call sites: `InterfaceEndpoint.get` (synchronous DB read — no proxy needed),
and the two network delete endpoints (now 202+poll, dispatched via the
queue). The decorator remains on `NetworkPingEndpoint.get` because the ping
handler executes `ip netns exec <network_uuid> ping` directly and the
network namespace exists only on the elected network node. Migrating the
ping endpoint to be queue-based requires new op-output infrastructure
(today the queue carries only error reports, not command output) and is
deferred to future work. The decorator definition in
`shakenfist/external_api/base.py` is retained for this one remaining use.

**Client-python.** `delete_network` and `delete_all_networks` in
`apiclient.py` (sibling `client-python` repo) handle the 202 response
transparently by default: they detect 202, extract the op UUID, and poll
`GET /clusteroperations/<op_type>/<op_uuid>` until the op reaches a
terminal state, raising `ClusterOperationFailed` on error. Advanced callers
can opt out of polling with `wait=False` to receive the op handle directly.
Two client methods `get_cluster_operation_chain` and
`list_cluster_operations_for_target` expose the discovery endpoints.

**VDI console proxy endpoints.** `GET /instances/<ref>/vdiconsoleproxy`
(`external_api/instance.py`, `InstanceVDIProxyConsoleHelperEndpoint`) mints a
short lived Ed25519 JWT and returns `{url, expires_at}` where `url` is
`<KERBSIDE_URL>/sf-console.vv?token=<jwt>`. It returns 404 when the Kerbside
integration is unconfigured, 406 unless the instance is `created`, 409 unless
the console is SPICE, and 500 when no signing key exists. `GET
/admin/vditokenpubkey` (`external_api/admin.py`,
`AdminVDITokenPublicKeyEndpoint`) publishes the public verification keys. See
the VDI console token trust model in [security_model.md](security_model.md).
