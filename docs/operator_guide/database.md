# Database Architecture

Shaken Fist uses MariaDB as its sole data store. This page describes the
bring-your-own MariaDB setup workflow, the compatibility requirements, the
configuration keys that control how cluster nodes reach the database tier,
the administrative commands, the table inventory, and the schema system.

## Bring-your-own MariaDB setup

Shaken Fist does not bundle or install MariaDB. The operator provisions the
database server before deploying the cluster.

### Provisioning checklist

1. **Provision a MariaDB server.** Any host reachable from every SF node
   works — it need not be an SF node itself. The server must meet the
   [compatibility requirements](#mariadb-compatibility-requirements) below
   (MariaDB 10.11.0+, InnoDB, utf8mb4).

2. **Apply the bootstrap snippet.** The repository ships
   `tools/bootstrap-mariadb.sql`, which creates the `shakenfist` database,
   the `shakenfist` user, and the required grants. Replace
   `__REPLACE_ME__` with the password you want:

    ```bash
    sed 's/__REPLACE_ME__/your-password/' tools/bootstrap-mariadb.sql | mysql -u root
    ```

    The snippet is idempotent and safe to re-run.

3. **(Optional) install the recommended tuning.** `examples/mariadb-tuning.cnf`
   ships a set of starting-point InnoDB and connection-pool settings tuned for
   a small-to-medium SF cluster. Copy it and restart MariaDB:

    ```bash
    sudo cp examples/mariadb-tuning.cnf /etc/mysql/mariadb.conf.d/
    sudo systemctl restart mariadb
    ```

    The values are starting points, not prescriptions — adjust them to match
    your hardware and workload.

4. **Deploy Shaken Fist.** Set the `mariadb_host`, `mariadb_port`,
   `mariadb_user`, `mariadb_password` and `mariadb_database` variables in your
   deployment's `group_vars/all.yml` to match what you provisioned, then run
   the deploy playbook (see [Installation](installation.md)). Only
   database-tier nodes render these values into `/etc/sf/config`.

5. **Schema initialisation.** The example playbooks run
   `sf-ctl ensure-mariadb-schema` themselves (once, delegated to a
   database-tier node) before any node registers, so no manual step is
   needed on first deploy. You can also run it manually at any time from a
   node that has `MARIADB_HOST` configured (see
   [Administrative Commands](#administrative-commands)).

### Single-box example

For a single-machine deployment, the complete workflow is:

```bash
sudo apt install mariadb-server
sed 's/__REPLACE_ME__/mypassword/' tools/bootstrap-mariadb.sql | sudo mysql -u root
sudo cp examples/mariadb-tuning.cnf /etc/mysql/mariadb.conf.d/   # optional
sudo systemctl restart mariadb
ansible-playbook -i examples/single-node/inventory.yaml examples/single-node/site.yml
```

(having first set the `mariadb_*` variables in
`examples/single-node/group_vars/all.yml` to match — see
[Installation](installation.md)).

## MariaDB compatibility requirements

Before `sf-database` starts, and before `sf-ctl ensure-mariadb-schema` applies
any schema work, the server is checked against these requirements:

- **MariaDB, not MySQL.** The `VERSION()` string must contain `MariaDB`.
  Shaken Fist uses MariaDB-specific column types (such as `INET4`) that are
  not available in MySQL.
- **Version 10.11.0 or later.** The `ipam_reservations` table uses the
  `INET4` column type, which only exists from MariaDB 10.10, and 10.11
  is the oldest in-support LTS above that. It is also the version the
  functional CI suite exercises, and ships with Debian 12/13 and
  Ubuntu 24.04.
- **Default storage engine: InnoDB.** Shaken Fist relies on row-level
  locking and transactional semantics provided by InnoDB.
- **Default character set: utf8mb4.** Required for full Unicode support,
  including supplementary characters.
- **Default collation: any `utf8mb4_*` collation.** The exact collation
  within the `utf8mb4` family is not mandated.

`sf-ctl ensure-mariadb-schema` runs these checks before touching any schema
objects and refuses to proceed if the server does not meet them, printing a
multi-line error that lists every failing check. The same checks run at
`sf-database` startup; the daemon refuses to start on an incompatible server.

After an SF version bump that includes schema changes, you must run
`sf-ctl ensure-mariadb-schema` **before** starting `sf-database`. If you
skip this step, the daemon will refuse to start with a schema-version
mismatch error that names the command to run.

## Why MariaDB and what it stores

MariaDB is the sole data store for Shaken Fist. All object state, IPAM
reservations, cluster operations, work queues, locks, metrics, and cluster
configuration live there. The single-store shape gives the system efficient
indexed queries by object type and state value, atomic IP-address reservation
via database uniqueness constraints, and transactional operation enqueue (the
cluster-operation header, the state row, and the queue row are written
atomically in a single transaction).

Only the database service daemon (`sf-database`) has direct access to MariaDB.
All other daemons reach MariaDB through `sf-database`'s gRPC interface. This
keeps connection management in one place, gives consistent Prometheus metrics
for every database operation, and makes the tier independently scalable. The
`shakenfist.mariadb` module dispatches automatically: `sf-database` and
`sf-ctl` use direct SQLAlchemy access when `MARIADB_HOST` is set, and every
other process goes over gRPC to the `sf-database` tier listed in
`MARIADB_GATEWAY_HOSTS`. The dispatch is per-process, not per-node, because
`MARIADB_HOST` lives in `/etc/sf/config` — the shared systemd
`EnvironmentFile` for every daemon on the node — so on a database-tier node
all of them can see it and only those two may act on it.

There is one exception, and it is worth knowing about when reading the tier's
connection counts. Cluster configuration is bootstrapped by
`config.load_cluster_config()`, which runs at import time — before a process
has established its identity — and so reads the `cluster_config` table
directly whenever `MARIADB_HOST` is visible, whatever the process. On a
database-tier node every daemon therefore makes one direct MariaDB connection
as it starts. It is a single short-lived read per daemon start, it does not
appear in `database_requests_total`, and nothing after startup uses that path.

The driver layer uses the `mariadb://` SQLAlchemy dialect so MariaDB-specific
column types such as `INET4` (4-byte IPv4 storage with native comparison and
indexing) are available. The underlying client library (`mysqlclient`)
remains the same because MariaDB maintains MySQL protocol compatibility.

### SQL Filter Pushdown

Object iteration uses a single indexed SQL query per call rather than materialising all rows and filtering in Python.

The filter criteria shape is `ObjectFilterCriteria` in
[`shakenfist/schema/object_filter.py`](https://github.com/shakenfist/shakenfist/blob/develop/shakenfist/schema/object_filter.py):

```python
from shakenfist.schema.object_filter import ObjectFilterCriteria

criteria = ObjectFilterCriteria(
    states=['created'],          # None means no state filter; [] is a no-op at the SQL layer
    namespace='tenant-a',        # None means no namespace filter
    name=None,                   # None means no name filter
    network_uuid=None,           # FK filter — see NetworkInterface special case below
    instance_uuid=None,          # FK filter — see NetworkInterface special case below
)
```

`None` on any field is "do not filter on this field". An empty list on `states` behaves the same as `None` at
the MariaDB layer, but callers may pass `[]` to express "no matching states" explicitly for future use.

**The `find_*` primitives.**
Four public functions in `shakenfist.mariadb` follow the naming convention `find_<type>`:
`find_artifacts`, `find_instances`, `find_networks`, and `find_network_interfaces`. Each one JOINs the
per-type static-values table to `object_states` on `uuid` and `object_type`, then applies whichever of the
three optional WHERE clauses the criteria specifies. The JOIN is always covered by the composite index
`idx_object_states_type_state` on `(object_type, state_value)`. The per-type `name` and `namespace` columns
each have their own single-column index on the type table.

**When to use which entry point.**

Name lookups from REST handlers should call the per-type `from_db_by_ref(name, namespace=ns)` class method
(e.g. `Artifact.from_db_by_ref(ref, namespace=ns)`). The override pushes
the name equality predicate to SQL.

Bulk iteration scoped by state and/or namespace should use the iterator constructor directly:
`Artifacts(namespace=ns, prefilter='active')`, `Instances(namespace=ns)`, `Networks(namespace=ns)`.
The iterator's `_find` override builds an `ObjectFilterCriteria` from the constructor arguments and delegates
to the appropriate `find_*` primitive, so both state and namespace reach SQL without a second round-trip.

Arbitrary-predicate filtering — logic that has no simple SQL equivalent, such as
`namespace_or_shared_filter` which must JOIN the `artifact_attributes` table to check the `shared` flag —
should pass a callable to the `filters=` argument of the iterator, or call `.filter([predicate])` on the
class. These predicates execute in Python after the indexed SQL scan returns its rows.

**NetworkInterface special case.**
The `network_interfaces` table has no `namespace` or `name` column. `find_network_interfaces` therefore
strips both fields from the criteria before building the query; they are silently ignored. State pushdown
still works. The two FK filter fields `network_uuid` and `instance_uuid` *are* honoured — they map to
indexed columns on the `network_interfaces` table, and they are how `Network.networkinterfaces` and
`Instance.interfaces` resolve their per-parent NI list (those properties
return hydrated `NetworkInterface` objects rather than the cached UUID list that used to live on the
attribute table). The other `find_*` helpers leave the FK fields at their default of `None` because the
underlying tables have no matching column.

See the Future-work entry in
[`docs/plans/PLAN-sql-pushdown-filtering.md`](../plans/PLAN-sql-pushdown-filtering.md) ("NetworkInterface
namespace column") for the deferred discussion of whether to add the column or use a JOIN-based approach
once a concrete caller exists.

**Example.**

```python
from shakenfist import mariadb
from shakenfist.schema.object_filter import ObjectFilterCriteria

criteria = ObjectFilterCriteria(states=['created'], namespace='tenant-a')
for a in mariadb.find_artifacts(criteria):
    ...
```

## MARIADB_HOST vs MARIADB_GATEWAY_HOSTS

These two config keys are orthogonal and serve different purposes. Understanding
the distinction helps when troubleshooting or planning a deployment.

**`MARIADB_HOST`** is set only on nodes that have *direct* access to the MariaDB
server. In practice this means nodes running `sf-database`, and any node where
an operator runs `sf-ctl ensure-mariadb-schema`. It lets `sf-database` and
`sf-ctl` bypass the gRPC layer and talk to MariaDB directly using SQLAlchemy;
no other process does so for its ongoing work, even when it can see the key —
the sole exception being the import-time cluster-config bootstrap described
above. Ordinary cluster nodes
(running `sf-api`, `sf-queues`, etc.) do **not** have `MARIADB_HOST` set and
should never need it.

**`MARIADB_GATEWAY_HOSTS`** is set on every cluster node. It is the list of
`sf-database` gRPC endpoints that non-database daemons connect to. For a
single-replica deployment this list has one entry; for higher availability,
list multiple `sf-database` endpoints and the gRPC client library round-robins
requests across them.

A node running `sf-database` has **both** keys set: `MARIADB_HOST` for its own
direct MariaDB access, and `MARIADB_GATEWAY_HOSTS` so that any client library
running on the same node can still reach the database tier over gRPC (for
example, when `sf-api` and `sf-database` are co-located).

In summary:

| Who uses it | Config key | What it does |
|-------------|------------|--------------|
| `sf-database`, schema tool | `MARIADB_HOST` | Direct SQLAlchemy → MariaDB |
| All other daemons | `MARIADB_GATEWAY_HOSTS` | gRPC → `sf-database` tier |
| `sf-database` itself (gRPC listener) | `MARIADB_GATEWAY_PORT` | Port each `sf-database` binds on (default 13005) |
| Prometheus scraper | `MARIADB_GATEWAY_METRICS_PORT` | Metrics port on each `sf-database` replica (default 13006) |

**Multi-replica deployments**: More than one `sf-database` replica can run
against the same MariaDB server. List every replica's mesh IP in
`MARIADB_GATEWAY_HOSTS`, comma-separated — for example,
`MARIADB_GATEWAY_HOSTS="10.0.0.20,10.0.0.21,10.0.0.22"`. Every `sf-database`
replica must be able to reach the MariaDB server; in BYO deployments this
typically means the operator's MariaDB is bound to a routable interface rather
than `127.0.0.1`. This multi-replica shape is exercised by CI on every
merge-queue run, so operators can rely on it as a supported production
configuration.

**Load balancing**: When `MARIADB_GATEWAY_HOSTS` is a multi-element list, every
SF daemon connects to the tier with a gRPC channel that round-robins requests
across the listed endpoints. Dead endpoints are skipped automatically: the
round-robin policy avoids subchannels whose TCP connection is down, and
aggressive client keepalives (a ping every 10 seconds with a 5 second
timeout) detect a hung replica within about 15 seconds. There is no
external load balancer to configure -- the round-robin behaviour and
failure detection are inside the gRPC client library. `sf-database` also
publishes the standard `grpc.health.v1.Health` protocol against the
empty-string service name for external monitoring via unary `Check` calls.
Watch-based client-side health checking (`healthCheckConfig`) is
deliberately not enabled: the synchronous health servicer can deadlock the
gRPC server's event thread when Watch streams open and close concurrently.

### Static object value cache

Each SF process caches the immutable static values of objects (their
`get_<type>()` results — uuid, name, version and other create-time columns)
in memory, so repeatedly loading the same node, blob or instance does not hit
`sf-database` every time. Mutable data (object state, metadata, attributes,
IPAM, daemon states) is never cached. The cache invalidates itself when this
process updates or deletes an object; a change made by another process is
picked up when the entry's TTL expires. On a multi-gateway tier that means a
delete routed to one gateway leaves the others serving the row to their own
clients until the TTL runs out.

Three settings control it:

| Setting | Default | Applies to |
|---------|---------|-----------|
| `OBJECT_CACHE_TTL_IMMUTABLE` | 300 | instance, network, networkinterface, agentoperation, ipam (static row changes only on create, delete or a version upgrade) |
| `OBJECT_CACHE_TTL_MUTABLE` | 30 | node, blob, artifact, upload, dnsmasq, namespace (rewritten only by an online version upgrade) |
| `OBJECT_CACHE_MAX_ENTRIES` | 20000 | how many entries a process retains, across both tiers |

The first two are in seconds. The third is a count, and it exists because a
TTL bounds how *stale* an entry may be, not how many entries are kept: expiry
only happens when that same object is looked up again, so an object read once
and never read again would otherwise sit in memory for the life of the
process. If you see `database_object_cache_capacity_evictions_total` climbing
steadily, the working set no longer fits and the hit rate is suffering;
`database_object_cache_entries` reports current occupancy.

When the cap is reached the cache drops expired entries first and then, if
that is not enough, live entries nearest to their expiry. Be aware of what
that means with two TTL tiers in one pool: a 30 second mutable entry is
always nearer its expiry than a 300 second immutable one inserted up to 270
seconds earlier, so sustained capacity pressure sheds essentially the whole
mutable tier — blob, node, artifact, namespace, upload, dnsmasq — before it
touches a single instance, network or ipam entry. If blob caching in
particular matters to your workload, raise the cap rather than relying on
the tiers competing for the space.

Setting a TTL to `0` disables caching for that tier — a fast rollback to
pure read-through that needs only a config change and a restart, no code
change. The `database_object_cache_hits_total`,
`database_object_cache_misses_total` and `database_object_cache_evictions_total`
Prometheus counters (labelled by `object_type`) report cache behaviour, and a
working cache shows up as reduced `database_get_<type>_total` rates on the
`sf-database` tier.

These counters only reach Prometheus from the daemons which serve a metrics
endpoint — `sf-cluster`, `sf-resources` and `sf-database`. Every process that
talks to the database has its own cache, so the numbers you can scrape are a
sample of the cluster's caching rather than a total; `sf-api` in particular
is invisible here.

Expect that rollback to set off database load alerts while it is in effect,
and plan for it rather than being surprised by it. The load budget was
derived with the cache on, so the pairs the cache suppresses now sit under
the budget's inclusion cut and are policed by the unbudgeted-polling ceiling
of 0.05/s per node instead of by an entry of their own. `GetIPAM` from
`sf-cluster` read 5.5/s before the cache and 0.02/s after it, and turning the
cache off puts it — and a good many pairs like it — two orders of magnitude
over that ceiling, firing `ShakenFistUnbudgetedDatabasePolling` for each.
The alert is not wrong: pure read-through really does cost that much, which
is the reason the cache exists. Silence the alert for the duration of the
rollback. Do not raise the budget to accommodate it, because the same file
also feeds `sf-ctl database-load` and the nightly report, and a level edited
to quiet an alert stops being a description of a healthy cluster. The alert
waits an hour before firing, so a short restart-and-observe will not trip it.

### Attributing database load to callers

Alongside the per-operation `database_<op>_total` counters, sf-database
publishes `database_requests_total{operation, caller_daemon}` — the same
request stream, but labelled with the daemon that issued each call. The
calling daemon is carried as gRPC metadata (`caller-daemon`) stamped by every
SF client and counted by a server-side interceptor, so the attribution is
complete even for daemons (sf-api, sf-net, sf-queues, …) that expose no
metrics endpoint of their own. A caller that never identified itself, or a
one-shot such as the config bootstrap, shows up as `caller_daemon="unknown"`.

Use it to answer "which daemon drives operation X", for example the hottest
callers overall:

```promql
topk(15, sum by (caller_daemon, operation) (rate(database_requests_total[5m])))
```

The existing `database_<op>_total` counters are unchanged — this metric is
additive — so summing `database_requests_total` by `operation` should track
the matching `database_<op>_total` rate (the `operation` label is the
PascalCase RPC name, e.g. `GetNode`, so it reads as the CamelCase form of the
counter suffix). The `caller-node` metadata is also sent but not yet a label;
it is reserved for the mTLS peer-identity cross-check.

### Understanding database load

"Is this much database load normal?" has no useful absolute answer,
because Shaken Fist's load is mostly polling and polling rates are set by
how many things exist rather than by how much work anybody is doing. A
number that is right for a six node cluster running eight instances is
wrong for everybody else. So what ships instead of a number is a model,
in `shakenfist/data/database_load_budget.yaml`:

```
expected_qps = per_node_base_qps x nodes
             + cluster_base_qps                (once for the whole cluster)
             + per_instance_qps x standing_instances
```

with one entry per `(operation, caller_daemon)` pair, each carrying a note
naming the loop that produces it. There are three terms because there are
three kinds of load. Most of it is a loop running on every node, so it
scales with cluster size. Some is the elected cluster daemon's maintenance
sweep, which runs once cluster-wide however many nodes you have. The rest
scales with standing instances, because it is work done per instance,
interface or blob.

What the model is for is telling apart the two reasons load goes up: you
grew, or something broke. An absolute ceiling cannot do that, and it is
why the first thing to check when the tier looks busy is the cluster's
shape rather than the queries per second.

#### Checking your cluster

```bash
sf-ctl database-load
```

Scrapes every gateway in `MARIADB_GATEWAY_HOSTS` twice over a minute and
prints what the tier is serving next to what the model predicts for your
cluster, sorted by how far over budget each pair is.

Most of the pairs a cluster serves have no budget entry at all, because
they are activity driven and near zero when nothing is happening -- around
three hundred of them on a cluster the size of ours. The table leaves
those out unless one is above its ceiling; `--all-pairs` prints them. The
`--json` output always carries every pair, so it is the one to attach to a
bug report.

Two flags in that output mean "expected, do not report":

* `provisional:#NNNN` -- the level is a known defect rather than a floor
  worth defending. Read the issue: it may already be fixed, in which case
  the entry over-predicts until the budget is next re-derived. Either way
  the pair is reported and never enforced.
* `activity` -- the level is set by what you and your tooling do rather
  than by one of our loops, so only you can say whether it is reasonable.

If a gateway does not answer, the command says which and reports on the
rest. It never quietly reports part of the tier as the whole of it,
because a total missing a gateway reads as load having fallen.

The footer distinguishes two things, because they carry different weights
of evidence. A *budgeted* pair above its ceiling is measured against a
model of your cluster and is worth reporting. A pair with *no* budget
entry above the unbudgeted ceiling has only been seen for one short
window, and a burst of ordinary work looks the same over sixty seconds as
a new polling loop does; the command asks you to re-run with a longer
`--window` first. The Prometheus alert for the same thing wants an hour
of it before it fires, for the same reason.

An absent entry means the pair was too quiet to model on the cluster the
budget was derived from, not that it ought to be near zero. A cluster
whose workload mix differs from that one -- fetching blobs harder, say --
can run such a pair well above the ceiling with nothing wrong, which is
why both this footer and the alert ask for more evidence before you act
on one.

The unbudgeted ceiling is itself a model rather than a number: the pairs
left out of the budget are mostly per-node loops, so the ceiling is
`unbudgeted_fixed_rate_per_node_qps` per node with
`unbudgeted_fixed_rate_qps` as a floor for small clusters. A flat
threshold would be one that ordinary traffic crosses on a large enough
cluster, permanently, with nothing wrong -- and an alert that always fires
gets silenced, while a silenced alert still reads as coverage.

#### Standing monitoring

[`examples/prometheus-database-load-rules.yaml`](https://github.com/shakenfist/shakenfist/blob/develop/examples/prometheus-database-load-rules.yaml)
is a drop-in Prometheus rule file, generated from the same budget, with
installation instructions in its comments. It records the model as
`sf_database:modelled_rate` and the measurement as
`sf_database:request_rate`, and carries three alerts: a budgeted pair
well above its model, a pair nobody budgeted for polling steadily, and
one for the model going blind because `instances_active` is not being
scraped.

The alerts compare a one day rate against a one day average of the model
(`sf_database:modelled_rate:1d`), rather than against the model evaluated
right now. Both halves have to cover the same window: a cluster that
halves its standing instance count overnight would otherwise have every
per-instance pair sitting above an immediately-shrunken ceiling until the
measurement caught up, for up to a day, which `for: 1h` does not cover and
which is not a regression. That last one matters: without `sf-resources` scraped the
modelled series are empty and neither of the other two can ever fire,
which looks exactly like a healthy cluster.

[`examples/grafana-dashboard.json`](https://github.com/shakenfist/shakenfist/blob/develop/examples/grafana-dashboard.json)
has matching panels for load by caller, measured against modelled, and
the count of pairs over budget.

On the measured-against-modelled panel, watch the enforced pair of lines
rather than the totals. The totals include every `activity` pair, and
those coefficients were fitted against the API traffic of the cluster the
budget was derived from -- which is ours, and is mostly CI. If your users
and tooling call the API differently, and they will, those two lines
diverge steadily and permanently without anything being wrong. The
`enforced` lines cover only the pairs produced by Shaken Fist's own loops,
so they are comparable across deployments, and they are the ones which
should track each other as the cluster grows.

#### When a pair is over budget

Please report it, with the output of `sf-ctl database-load --json`
attached, at
[the issue tracker](https://github.com/shakenfist/shakenfist/issues). A
pair well above its model is usually one of two things: a loop of ours
that has stopped batching a read it used to batch, or a workload shape the
model does not describe. Both are worth knowing about, and the second is
how the shipped coefficients get better for clusters that are not ours.

Do not edit the budget to make an alert stop. A budget that tracks
whatever the code currently does is not a budget.

### Monitoring sf-database with grpc-health-probe

`sf-database` reports live MariaDB reachability through the standard
`grpc.health.v1/Check` RPC on the empty-string (`""`) service name.
Operators can probe this with `grpc-health-probe`:

```bash
grpc-health-probe -addr=<sf-database-host>:13005
```

The status reflects the outcome of the most recent ~10 s background poll:
`SERVING` means `sf-database` can reach MariaDB; `NOT_SERVING` means the
last poll failed. Schema currency (whether the schema is up to date) is
only checked at startup and is not a runtime signal — a running
`sf-database` replica always has an up-to-date schema.

`sf-api` consumes this signal through its per-worker readiness checker
(`shakenfist/external_api/health.py`). When `sf-database` reports
`NOT_SERVING`, the checker flips the cached flag after three consecutive
failures and `sf-api`'s `/readyz` endpoint begins returning `503`. A load
balancer probing `/readyz` will then drain the `sf-api` worker before
MariaDB connectivity is restored. Recovery is asymmetric: once
`sf-database` recovers and the checker sees a single `SERVING` response,
`/readyz` returns `200` and the worker is restored to rotation
automatically.

### Client behaviour during an outage

When a daemon cannot reach any `sf-database` instance, its gRPC calls
retry up to three times on UNAVAILABLE / DEADLINE_EXCEEDED and then raise
`shakenfist.exceptions.DatabaseUnavailable`. The gRPC channel is only
rebuilt between attempts on DEADLINE_EXCEEDED (the wedged-subchannel
signature); on UNAVAILABLE the warm round_robin channel is kept so a
surviving gateway can serve the retry while the failed one reconnects in
the background. This is deliberately distinct
from the "object not found" return values the client library uses for
genuinely missing objects, so code never mistakes an outage for a missing
object and, for example, cleans up something that still exists.

What an operator sees during an outage:

- Daemon work loops log the `DatabaseUnavailable` errors and retry; they
  do not exit. The queues daemon explicitly pauses queue processing and
  waits for its health checks to pass again.
- Held cluster locks survive short outages: the lock refresher retries
  every ~2s and the lease only lapses if the outage outlives it (see the
  [locks documentation](locks.md)). Lock *acquisition* keeps retrying
  inside the caller's timeout.
- `sf-api` returns 500s for requests that need the database, and its
  `/readyz` endpoint goes 503 (via the `sf-database` health signal above)
  so load balancers drain it until the database returns.

Recovery is automatic once an `sf-database` instance is reachable again;
no daemon restarts are required.

### Rolling restarts of the database tier

The deployer restarts database-tier nodes one at a time (`serial: 1`),
and three mechanisms cooperate so cluster clients ride through the roll
without a visible outage:

- **Graceful drain on stop.** When `sf-database` receives a stop it flips
  its health status to `NOT_SERVING`, stops accepting new RPCs, and then
  lets in-flight RPCs finish for up to `DATABASE_DRAIN_GRACE` seconds
  (default 10) before forcing the server down. The grace is a cap, not a
  fixed delay — shutdown proceeds as soon as the last in-flight call
  ends. If you raise it, keep it comfortably below the systemd unit's
  `TimeoutStopSec` (30s) or systemd will SIGKILL the daemon mid-drain.
- **A health gate before the next node restarts.** After (re)starting
  `sf-database` the deploy runs `sf-ctl gateway-health` (see
  [gateway-health](#gateway-health) below) and retries until the gateway
  reports `SERVING`, so the next node in the roll is never taken down
  while this one is only half-up (listening on its port but unable to
  reach MariaDB yet).
- **A settle pause so clients reconnect.** Once the gateway is healthy,
  the deploy pauses for `sf_database_roll_settle_seconds` (an Ansible
  variable, default 10) before moving to the next node. Cluster clients
  re-establish their round_robin subchannel to the recovered gateway on
  their own reconnect backoff; without the pause, the next restart could
  briefly leave a client with no READY backend at all. The pause is
  skipped on idempotent no-op deploys that did not restart anything.

## Administrative Commands

The `sf-ctl` command provides several database-related administrative functions.
These commands are typically used during cluster bootstrap and maintenance.

### ensure-mariadb-schema

Ensures the MariaDB schema exists and is up to date. This command must be run
on a node with direct database access (i.e. `MARIADB_HOST` configured):

```bash
sf-ctl ensure-mariadb-schema
```

The command first performs a compatibility check against the requirements
listed in [MariaDB compatibility requirements](#mariadb-compatibility-requirements)
above, then creates any missing tables and applies pending schema migrations.
Operators must run this command (or ensure their deployment automation runs
it) before starting `sf-database` whenever an SF upgrade includes schema
changes.

Daemon upgrade ordering matters too: **upgrade the `sf-database` tier before
`sf-api` and the queue daemons**. Instance placement is admitted by a
database-service RPC (`AdmitInstancePlacement`) which has no client-side
fallback -- a new `sf-api` calling an old `sf-database` receives gRPC
UNIMPLEMENTED, which is deliberately treated as a failed write rather than
as "the cluster is full", so every instance create returns HTTP 500 until
the database tier catches up. The error string names the condition
(`database service predates AdmitInstancePlacement`). A deployment that
rolls every node in one pass keeps this window to minutes at most, but a
staged rollout that upgrades API nodes and leaves the database tier on
the old release stays broken until the tier is upgraded -- do the
database tier first.

### initialise-node

Creates a node record in the database. By default, it uses the local node's
configuration:

```bash
sf-ctl initialise-node
```

From a database-tier node (one with `MARIADB_HOST` already in
`/etc/sf/config`), the command can initialise any node in the cluster without
any env-var prefix:

```bash
sf-ctl initialise-node --node-name sf-2 --node-mesh-ip 10.0.0.2
```

### gateway-health

Checks that an `sf-database` gateway reports `SERVING` via the standard
`grpc.health.v1/Check` RPC, exiting zero on `SERVING` and non-zero
otherwise. On `sf-database`, `SERVING` means MariaDB is reachable and the
schema was current at startup — a stronger signal than the port merely
accepting connections. By default it probes this node's own gateway
(`NODE_MESH_IP`):

```bash
sf-ctl gateway-health
sf-ctl gateway-health --host 10.0.0.2 --timeout 5
```

This is the health gate the deploy's serial rolling restart waits on
before moving to the next database-tier node (see
[Rolling restarts of the database tier](#rolling-restarts-of-the-database-tier)
above). It is equivalent to a `grpc-health-probe` against port 13005, but
needs no extra binary installed on the node.

### register-daemon

Registers one or more daemons on a node. By default, it registers on the local
node:

```bash
sf-ctl register-daemon sentinel-first privexec nodelock
```

From a database-tier node (one with `MARIADB_HOST` already in
`/etc/sf/config`), daemons can be registered against any node in the cluster:

```bash
sf-ctl register-daemon database --node-name sf-1
```

## MariaDB Table Inventory

The MariaDB schema uses different table patterns depending on the data
characteristics. This section is a developer- and operator-facing reference
for the per-table layout.

### Table Architecture

#### Shared Tables (DatabaseBackedObject level)

Data that has the same schema across all object types is stored in shared
tables with `(object_type, object_uuid)` keys:

| Table | Purpose |
|-------|---------|
| `object_states` | State value, update time, message for all objects |
| `object_metadata` | User-defined metadata for all objects |

These tables are efficient for cross-type queries (e.g., "find all objects
in error state").

#### High-Churn Dedicated Tables

Some data has high write frequency or requires atomic operations with database
constraints. These get dedicated tables optimized for their access patterns:

| Table | Purpose |
|-------|---------|
| `ipam_reservations` | IP address allocations with uniqueness constraints |
| `cluster_operations` | Full cluster operation metadata with indexed `node_uuid`, `instance_uuid`, `network_uuid` and `priority` columns extracted from JSON for dispatch-time filtering |
| `work_queue` | Per-job queue row with `queue_name`, `scheduled_at`, `claimed_at`, `claimed_by`, `attempts` and `payload`. Dequeue uses `SELECT ... FOR UPDATE SKIP LOCKED` |
| `cluster_operation_targets` | Operation-to-object targeting with AUTO_INCREMENT ordering |
| `cluster_operation_errors` | One row per failed cluster operation, keyed by `op_uuid`. Stores the structured `ErrorReport` (code, message, details, origin_class, traceback) JSON. Cleaned up alongside the `cluster_operations` row by `BaseClusterOperation.hard_delete()` when the cluster cleaner reaps a terminal-state op |
| `node_metrics` | Ephemeral per-node resource metrics with semi-schemaless JSON payload, plus typed nullable columns projecting the capacity-relevant fields and the node's hypervisor role |
| `node_daemon_states` | Per-`(node, daemon)` state rows; atomic upsert per daemon, no Python-side coarse lock |
| `cluster_locks` | Leased distributed locks. `expires_at` lets candidates steal a dead holder's lock without external GC; holders refresh every ~20 s while alive |
| `scheduler_node_capacity` | One row per schedulable hypervisor: limits derived from the typed `node_metrics` columns, materialised usage counters, and a decaying expected-demand signal |
| `namespace_claims` | One row per namespace capacity claim: limits, usage counters, coverage state and server-side expiry. Also the static-values table for the `NamespaceClaim` object |
| `cluster_capacity` | A singleton row (id always 1): cluster-wide totals, capacity claimed by active claims, and usage by namespaces without a claim |

IPAM reservations are stored separately because:

- **Atomic allocation**: Database uniqueness constraints prevent race conditions
- **High churn**: Addresses are frequently reserved and released
- **Cross-object queries**: Need to find all addresses for an IPAM, not just
  one object

`node_metrics` additionally projects its capacity-relevant fields (CPU,
memory, disk counts and the disk-busy bandwidth rate), plus the node's
`is_hypervisor` role flag, from `metrics_json` into typed nullable columns
at upsert time, so SQL-side capacity arithmetic (the
scheduler-reservations work) can query them directly instead of
unpacking JSON per row. The role flag is projected because the resources
daemon runs on every node and publishes metrics whatever that node's
roles are, so a query that sums schedulable capacity has to exclude
network-only and database-only nodes the same way the scheduler does.
`metrics_json` remains the full payload and stays
authoritative for readers; the typed columns are only a projection of it,
extracted server-side in `_direct_upsert_node_metrics()` so rows written by
an older resources daemon during a rolling upgrade still get their columns
populated once `sf-database` is upgraded. After running `sf-ctl
ensure-mariadb-schema` to add the columns (run it before rolling the
daemons, as always), existing rows keep NULL columns until the next 60
second upsert cycle repopulates every live node — no backfill needed for a
table whose rows are ephemeral by design.

The three capacity tables (`scheduler_node_capacity`, `namespace_claims` and
`cluster_capacity`) are recomputed wholesale from ground truth by a
reconciler that runs every five minutes on the elected cluster node, and —
since phase 3, described below — are also drawn down and released
incrementally by the placement admission and release RPCs between those
passes. The reconciler is therefore the drift corrector rather than the
only writer. Each pass is a single `ReconcileSchedulerCapacity`
RPC which expires stale claims, re-derives each hypervisor's limits from
the typed `node_metrics` columns (deliberately mirroring the scheduler's
admission arithmetic), recomputes usage counters from placed instances,
recomputes the decaying expected-demand signal, and rebuilds the
`cluster_capacity` singleton. The tables are created by `sf-ctl
ensure-mariadb-schema` (run it before rolling the daemons after an
upgrade, as always).

As of scheduler-reservations phase 3 the counters are consumed for
admission, not just observed. Instance placement goes through two
`sf-database` RPCs, `AdmitInstancePlacement` and
`ReleaseInstancePlacement`, each performing its guarded counter update,
the `placement` attribute write and the `instance_location` reference
rewrite in a single transaction, so a placement can never be recorded
without the capacity it consumes and two concurrent creates racing one
remaining slot admit exactly once. A denial names the failing stage —
the cluster or claim row, or a specific node's row — and the dimension(s)
that would have been exceeded (`cpus`, `memory_mb`, `disk_gb`, or the
`demand` feedforward term); the scheduler-driven callers (first
placement, the preflight redirect) walk to the next candidate on a
denial and, once every candidate is exhausted, return the ordinary 507
"cluster full" response with the denial detail attached to the audit
event. The `demand` term alone can never produce that 507: if the walk
admits nowhere but some candidate was refused only on demand, the
caller walks again with the demand clause waived, since demand exists
to spread bursts across nodes rather than to bound capacity (see the
scheduler operator guide). Not every writer enforces the guard: the cleaner's placement
rewrites and the queues daemon's startup reconciliation pass
`enforce=False`, because they record where a libvirt domain already
*is* — a guard cannot refuse reality, and refusing to record it would
just leave the ledger wrong. A non-enforced write that pushes a node
over its limit still updates every counter and emits a loud audit
event (`placement recorded despite exceeding capacity guard`) so the
overage is visible rather than silently absorbed. One exception: the
queues daemon's startup reconciliation records placements without
probing for the overage first, so it does not emit that event — the
cleaner probes and events, the startup repair records, and the
reconciler's next pass surfaces any excess in the drift figures. A node or the
cluster singleton missing its capacity row — mid-upgrade, or a cluster
whose reconciler has never run — fails open: placement proceeds
unguarded, an `instance placed without capacity guard` event records
it, and the reconciler's next pass creates the missing row. In every
case — enforced denial, unenforced overage, or fail-open admission —
the reconciler is the drift healer: whatever the guard let through or
refused, the next five-minute pass recomputes every counter from
placed, non-deleted instances and corrects it.

The admission and release transactions are compatible with
`innodb_snapshot_isolation` ON, the default from MariaDB 11.6.2 (what
Debian 13, Ubuntu 24.04 and every recent container tag ship). That took
moving every plain `SELECT` a transaction needs ahead of opening it, so
the transaction's first statement is always a guarded `UPDATE` — see
[the developer guide](../developer_guide/standards.md#a-guarded-update-must-be-the-transactions-first-statement)
for why a `SELECT` ahead of the `UPDATE` reintroduces ER_CHECKREAD
(1020) transaction aborts under that setting.

Operator-facing observability is the `scheduler_capacity_*` family of
prometheus metrics (per-node limit/used/expected-demand gauges, cluster-row
gauges, and reconcile pass/failure counters, last-success timestamp and
duration) exported from the cluster daemon's metrics port
(`CLUSTER_METRICS_PORT`, default `13007`), plus one structured log line
per reconcile pass.

Disk capacity is claimed at virtual size, which the phase 0 step 3
addendum measured at 40-140x actual usage (median ~65x) for sparse
qcow2 images, so a within-period burst of virtual claims would be
rejected against last-observed actual free space.
`SCHEDULER_DISK_OVERCOMMIT` (default 5.0) multiplies the free-space
headroom term of each node's derived disk limit — `used + max(0,
floor(free/GiB) - reservation) x SCHEDULER_DISK_OVERCOMMIT` — never
the used (drawdown) term and never a physical-disk total, since no
such metric exists. A genuinely full disk still admits nothing:
headroom goes to zero with free space regardless of the ratio. A
value at or below zero (including an unset field from a mid-upgrade
caller) falls back to 1.0, the pre-ratio arithmetic.

A node only has a capacity row while it could actually be scheduled
onto, so expect rows to appear and disappear as nodes change state. A
node loses its row when it stops being a hypervisor, when it leaves the
active states (so anything the node-health cascade takes out of service
stops contributing to the cluster totals), when it has no row in the
`nodes` table at all, or when its `node_metrics` row goes stale — more
than fifteen minutes without an update, which means the resources daemon
has stopped publishing even though the node itself still looks alive.
Each of those mirrors a filter the scheduler already applies before
considering a node as a placement candidate.

Severe clock skew is a fifth way to lose the row. The
`node_metrics.timestamp` column is written by each node's resources
daemon from that node's clock, and the staleness check runs on the
database daemon's, so a node running more than fifteen minutes slow
looks permanently stale. Any working NTP setup is far inside that, but
it is worth knowing if a node drops out of the capacity tables while
otherwise looking healthy.

Three things to know when reading those numbers. The `used_*` counters
are allocation ledgers: they sum what every placed, non-deleted instance
was allocated, so an instance that is powered off but not deleted still
counts, and the numbers will not match the resources daemon's
`cpu_total_instance_vcpus` and `memory_total_instance_actual` (which
count only running libvirt domains) on a cluster with powered-off
instances. The `cluster_capacity` singleton is a closed accounting over
the nodes that hold capacity rows: an instance stranded on a node that
has lost its row (errored, demoted, stale metrics, deleted) contributes
to neither the total nor the unclaimed-used side, so a drained
hypervisor makes both numbers shrink together rather than showing usage
exceeding capacity. A namespace claim's `used_*` counters are the
deliberate exception — they stay namespace-wide, because a quota covers
the namespace's instances wherever they are stranded.
And the gauges are published only by the elected cluster node,
which drops them when it loses the lock, so during a leadership handoff
there is a window with no capacity gauges at all until the new leader's
first pass. Alert on the reconciler falling behind rather than on a
gauge disappearing, and make the alert cluster-scoped:

```
time() - max(scheduler_capacity_reconcile_last_success_timestamp) > 900
```

The `max()` matters. Unlike the capacity gauges, the last-success
timestamp is not cleared on demotion — it records when *that node* last
reconciled successfully, which is useful for debugging — so every node
that has ever held and lost the maintenance lock keeps publishing its
own frozen value. A per-instance staleness alert would fire permanently
on all of them. Aggregating asks the question you actually want
answered: has *anybody* reconciled recently.

As of scheduler-reservations phase 4 the `namespace_claims` table has
writers other than the reconciler. Five `sf-database` RPCs —
`CreateNamespaceClaim`, `GetNamespaceClaim`, `GetNamespaceClaims`,
`UpdateNamespaceClaim` and `DeleteNamespaceClaim` — back the admin-only
REST endpoints at `/auth/namespaces/<namespace>/claims`, and the
`NamespaceClaim` object is persisted in this same table rather than a
separate static-values one.

Creating, growing and shrinking a claim are admission decisions in their
own right: a claim is a promise of capacity the cluster has to be able to
keep. Each therefore writes `cluster_capacity` and then
`namespace_claims` in one transaction, in the same canonical order and
with the same guarded-`UPDATE`-first discipline as instance admission, so
the new statements compose with placement rather than deadlocking against
it or dying of ER_CHECKREAD. A create or a grow is guarded, per
dimension, by

    claimed + limit + GREATEST(0, unclaimed_used - migrated) <= total

and a rowcount of zero is the refusal. `migrated` is the namespace's
existing drawdown being moved onto the claim; it is zero for a grow,
which moves nothing because the namespace's usage is already on the
claim's side of the ledger.

That migration is the reason for the term. A namespace's instances are
counted in `cluster_capacity.unclaimed_used_*` until it has a claim, so
creating one seeds the new row's `used_*` with the namespace's current
drawdown and subtracts the same amounts from `unclaimed_used_*` in the
same transaction that increments `claimed_*`. Deleting a claim migrates
it back, floored. Seeding zero instead would let a namespace with running
instances place its whole claim a second time until the next reconcile
pass. The drawdown is computed by the same aggregation the reconciler
recomputes with, so a new claim starts at the figure the next pass will
compute and that pass then moves nothing.

Two behaviours differ deliberately from instance admission:

- **A missing `cluster_capacity` singleton refuses rather than failing
  open.** Admitting an instance unguarded records where a machine already
  is; creating a claim unguarded writes a promise against totals nothing
  has computed, which the next pass folds into `claimed_*` whether it
  fits or not. An instance create cannot wait five minutes; a claim can.
  The REST layer reports this as a 503, not a 507 — the cluster is not
  full, the reconciler simply has not built the row yet. Only the
  statements that need a total are refused this way: a shrink or an
  expiry change, which claim nothing, go through.
- **Claim ceilings are advisory in this release.** The instance admission
  transaction does not enforce the claim guard: a placement that pushes a
  namespace past its claim is admitted, and the exceedance is detected by
  reading the claim row back inside the same transaction and reported in
  the reply, which `Instance` turns into a `placement admitted over
  namespace capacity claim` audit event. See [the scheduler operator
  guide](scheduler.md#namespace-capacity-claims) for the operator view.
  Phase 3's single fail-open flag was split into three for this: the node
  and cluster guards keep failing open on a node with no capacity row,
  because that reasoning is about *this node's* limits being absent from
  the totals, which says nothing about whether a namespace has exceeded
  what it claimed.

Cluster operation headers (`cluster_operations`) and work queue rows
(`work_queue`) live in MariaDB so the create-and-enqueue step can run in a
single transaction (header row + state row + queue row). The `work_queue`
table uses MariaDB row locking with `SELECT ... FOR UPDATE SKIP LOCKED` for
race-safe dequeue.

The cluster daemon runs
`reap_stuck_cluster_operation_jobs()` from
`shakenfist/daemons/cluster/scheduled_tasks.py` on a one-minute
schedule. It re-queues or rejects rows whose claim has gone stale:

- **`CLUSTER_OP_STUCK_THRESHOLD`** — seconds before a claimed row is
  considered stuck (default `1800`). Lower values detect crashed
  workers faster at the cost of possibly re-queuing merely slow jobs.
- **`CLUSTER_OP_MAX_ATTEMPTS`** — maximum claim attempts before the
  reaper stops re-queuing and transitions the underlying cluster
  operation to `STATE_ERROR` (default `5`). Protects the queue from
  a "job of death" that crashes every worker.
- **`CLUSTER_METRICS_PORT`** — Prometheus scrape port exposed by the
  cluster daemon (default `13007`). Metrics
  `cluster_op_reaper_requeued_total` and
  `cluster_op_reaper_rejected_total` record reaper activity.

The cluster daemon also runs a deleted-object sweep (every 15 minutes)
which hard deletes objects that have been in a final state (`deleted`,
`complete`, `abort`) for longer than their grace period (`CLEANER_DELAY`
for most objects, 30 seconds for completed operations). The sweep's
work queue holds `(object_type, uuid)` tuples — the candidate list is a
single SQL query per object type with the age filter applied
database-side, and objects are hydrated one at a time at processing
time, so a large backlog or a single failing object cannot stall the
pass.

### Watching for sweeps that silently do nothing

Every sweep in the cluster daemon starts by reading a work list, and a
sweep that cannot read its work list does nothing at all — no error, no
backlog drained, and nothing else retries on its behalf. That is how a
`node_inst_op` backlog grew until its own reply became too large to read
(#3638). The daemon therefore counts consecutive failed work-list reads:

- **`cluster_sweep_work_list_failure_streak`** — a gauge on
  `CLUSTER_METRICS_PORT`, labelled by `sweep` (`per_blob`,
  `per_instance`, `per_deleted_object`, `reconcile_orphans`) and
  `object_type`. It is the count of consecutive passes that could not
  read that work list, and is reset to zero by the first successful
  read.

The gauge describes elected-leader work, so only the node currently
holding the cluster maintenance lock publishes it; a node that loses the
election drops the label sets entirely rather than freezing them at
their final value, so a stale non-zero reading cannot outlive the
leadership it describes.

A label set appears only once that sweep has failed a read at least
once, so a healthy cluster exports no samples for this gauge at all, and
a sweep that has since recovered reads zero. Do not build an `== 0` or
`absent()` check on it, and do not read an empty query result as a
broken metric — that is what a cluster with nothing wrong looks like.
The same applies immediately after a leadership change, which drops the
label sets. A sample alert:

```
max by (sweep, object_type) (cluster_sweep_work_list_failure_streak) > 2
```

Two consecutive failures is noise (a database gateway restart lands
inside a single pass); a streak that keeps climbing means that sweep has
stopped running, and its backlog is growing. Check the cluster daemon
log for `could not read its work list` to see which shape of failure it
is — an unreachable database tier, or a reply too large for the client's
receive cap.

Two related skips have no metric of their own and are visible only in
logs. The cleaner's blob maintenance pass skips reclaiming disk on that
node when either of its two reads fails — grep for `could not read the
active blob list` or `could not read this node's blob locations` — and
`sf-cleaner` exports no Prometheus endpoint today. The cluster daemon's
`_cluster_wide_cleanup()` logs when it degrades to skipping just its
blob section while continuing the rest of the pass.

An hourly orphan reconciliation sweep handles rows the state-driven
iterators cannot see:

- **Phantoms** — `object_states` rows whose static-values row is gone
  (for example after a partially failed hard delete). These are deleted
  database-side, guarded by a one hour minimum row age so objects
  mid-creation are never raced. Orphaned `artifact_attributes` rows are
  removed the same way.
- **Zombies** — static-values rows with no `object_states` row
  (for example after a crash between static-row creation and the first
  state write). Once a zombie has been observed on two consecutive
  sweeps it is repaired by writing a `deleted` state row, after which
  the regular deleted-object sweep hard deletes it. Node and namespace
  objects are never auto-repaired.

Both sweeps log what they remove, and zombie repairs also emit an audit
event against the repaired object.

Cluster operation targets are stored separately because:

- **Append-only history**: Every operation enqueued against an object creates
  a row, giving full operation history per target
- **Automatic ordering**: AUTO_INCREMENT sequence_number replaces the implicit
  dependency chain traversal
- **Indexed queries**: Efficient lookups for "latest operation on this instance"
  and "all operations on this object in order"

Because the table is append-only, it is bounded by a periodic prune in the
cluster daemon (alongside the existing `delete_stale_transfers` cleanup).
The cluster daemon runs cluster-wide cleanup under `ClusterLock` election,
so the prune naturally runs from a single node at a time. The prune
removes rows whose `created_at` is older than
`CLUSTER_OPERATION_TARGET_RETENTION` seconds **and** whose operation is not
currently in an active state (`queued`, `preflight`, or `executing`) in
`object_states`. Operations still in flight are never pruned regardless of
age. Set `CLUSTER_OPERATION_TARGET_RETENTION` to 0 to disable pruning
entirely (the default is 7 days).

#### Cluster Operation Target Tracking

The `cluster_operation_targets` table holds one row per (operation, target
object) pair. Each row carries the target's object type and UUID, plus the
`operation_uuid` and an `AUTO_INCREMENT` `sequence_number` that gives
total ordering per target.

Two query shapes are exposed to the rest of the system:

- **`get_latest_cluster_operation_target`**: returns the highest-sequence
  row for a given `(object_type, uuid)` pair, regardless of state. Used
  by the `last_cluster_operation` property and `external_view()`
  projections to provide the familiar "which op ran last?" answer.
- **`has_pending_cluster_operation_target`**: returns `True` if any row
  for the object references an operation whose state is `queued`,
  `preflight`, or `executing`. Used by `Network.is_okay()` and any other
  gate that must defer while work is in flight. Because it checks all
  rows rather than only the latest one, a later terminal operation cannot
  mask an earlier in-flight one.

Rows are written automatically by `enqueue_cluster_operation`; operators
do not need to manage them. Pruning is performed by the cluster daemon
under `ClusterLock` election via
`_direct_delete_stale_cluster_operation_targets`: rows older than
`CLUSTER_OPERATION_TARGET_RETENTION` whose operation has reached a
terminal state are removed; in-flight operations are never pruned.

#### Per-Type Static Value Tables

Each concrete object type that is migrated gets its own table for static
values (immutable data set at creation time):

| Table | Object Type | Fields |
|-------|-------------|--------|
| `uploads` | Upload | uuid, node, created_at, version |
| `dnsmasq` | DnsMasq | uuid, namespace, owner_type, owner_uuid, provide_dhcp, provide_dns, version |
| `blobs` | Blob | uuid, modified, fetched_at, version |
| `nodes` | Node | uuid, fqdn (unique index), ip, version |
| `namespaces` | Namespace | name (VARCHAR PK), version |
| `namespace_keys` | NamespaceKey | uuid, namespace, name, version. UNIQUE index on (namespace, name), which also serves the per-namespace listing |
| `trusted_issuers` | TrustedIssuer | uuid, name, version |
| `mapping_rules` | MappingRule | uuid, namespace, name, version |
| `artifacts` | Artifact | uuid, artifact_type, source_url, name, namespace, version |
| `network_interfaces` | NetworkInterface | uuid, network_uuid, instance_uuid, macaddr, ipv4, order, model, version |
| `ipams` | IPAM | uuid, namespace, network_uuid, ipblock, version |
| `networks` | Network | uuid, name, namespace, netblock, provide_dhcp, provide_nat, provide_dns, vxid (unique), egress_nic, mesh_nic, version |
| `agent_operations` | AgentOperation | uuid, namespace, instance_uuid (indexed), commands (JSON list), deadline (nullable), progress_timeout (nullable), version |
| `instances` | Instance | uuid, cpus, disk_spec (JSON), memory, name, namespace (indexed), requested_placement (JSON), ssh_key, user_data, video (JSON), uefi, configdrive, nvram_template, secure_boot, machine_type, side_channels (JSON), version |

These tables use the object's UUID as the primary key, except for
`namespaces` which uses the namespace name (a string) as its primary key.

#### Per-Type Attribute Tables

Mutable attributes that are specific to an object type are stored in
dedicated attribute tables:

| Table | Object Type | Key Fields |
|-------|-------------|------------|
| `blob_attributes` | Blob | uuid, size, info, last_used, retention |
| `node_attributes` | Node | uuid, last_seen, installed_version, roles, daemons, versions, metrics. Per-daemon state lives in `node_daemon_states` since v19; the legacy `daemon_states` JSON column on this table is no longer read or written. Instance placement lives in `object_references` as `instance_location` rows since `object_references` schema v3; the dual-write and the union into reads were removed in scheduler-reservations phase 3; the legacy `instances` JSON column itself remains in place (nullable, unread) as a rollback fallback until a later release drops it |
| `namespace_attributes` | Namespace | name, keys (JSON), trust (JSON). Keys live in `namespace_keys` / `namespace_key_attributes` since the v2 `namespace_keys` migration; the legacy `keys` JSON column is left in place until a later schema bump drops it |
| `namespace_key_attributes` | NamespaceKey | uuid, key (base64 encoded bcrypt hash), nonce, expiry (nullable epoch seconds), scopes (nullable JSON list), provenance (nullable JSON dict) |
| `trusted_issuer_attributes` | TrustedIssuer | uuid, issuer_url, jwks_uri, audience |
| `mapping_rule_attributes` | MappingRule | uuid, issuer, bound_claims (JSON dict), scopes (JSON list), key_ttl, key_name_prefix |
| `artifact_attributes` | Artifact | uuid, max_versions, shared, highest_index |
| `artifact_indexes` | Artifact | artifact_uuid + index_number (composite PK), blob_uuid |
| `network_interface_attributes` | NetworkInterface | uuid, floating_address |
| `network_attributes` | Network | uuid, floating_gateway, hosteddns (JSON dict) |
| `agent_operation_attributes` | AgentOperation | uuid, results (JSON dict), last_progress (nullable), attempts |
| `instance_attributes` | Instance | uuid, placement (JSON), power_state (JSON), ports (JSON), enforced_deletes (JSON), block_devices (JSON), agent_state (JSON), agent_attributes (JSON), agent_operations (JSON), kvm_pid, error_message, vsock_cids (JSON dict) |

Node attributes consolidate observed state, roles, daemons and versions
into a single row.

Namespace attributes consolidate keys (authentication) and trust
(namespace trust relationships) into a single row.

Because these tables pack several logically independent attributes into
one row, every updater passes a field mask naming exactly the columns it
changed (see `update_*_attributes` in `shakenfist/mariadb.py`). Full-row
read-modify-write cycles are reserved for row creation and schema
upgrades: with concurrent writers on different nodes, an unmasked write
pushes a stale snapshot of the other columns over any update committed
since the writer read the row (a cross-attribute lost update).

An agent operation's `deadline` and `progress_timeout` are the caller's
timing intent, fixed at submission time. Three values are possible in
each column and they mean three different things:

- **NULL** — no client intent was recorded, so the server default
  applies. This is what a row written before deadlines existed looks
  like, and what a row written by a not-yet-upgraded API node during a
  rolling upgrade looks like. It does **not** mean "no deadline".
- **0** — the client explicitly asked for none: no wall-clock deadline,
  or the progress timeout disabled.
- **Anything else** — the client's request. `deadline` is an absolute
  unix timestamp, not a duration: the API server computes it at request
  receipt, so queue time and preflight time count against it.
  `progress_timeout` is a number of seconds.

A current API server always writes one of the last two, never NULL: the
caller's `deadline_seconds` and `progress_timeout_seconds` if they sent
them, and otherwise `AGENT_OPERATION_DEFAULT_DEADLINE` (600) and
`AGENT_OPERATION_DEFAULT_PROGRESS_TIMEOUT` (30) applied at request
receipt. The default is applied there rather than left to be resolved
later because a deadline runs from when the request arrived, and only
the API node knows when that was. So a NULL is now diagnostic: it says
the row came from a node which had not yet been upgraded.

An operation whose commands cannot report progress -- anything created
through `/instances/<ref>/agent/execute` -- records a `progress_timeout`
of 0 rather than the default, because a timeout there could never fire
and recording one would describe the operation as something it is not.

`last_progress` is the unix timestamp of the most recent observed
forward progress, NULL when none has been observed, and `attempts`
counts dispatches for the retry bound.

`deadline` and `progress_timeout` are enforced: an operation which
outlives either moves to the terminal `expired` state, checked at
dequeue, during preflight and in the sidechannel executor. A NULL
`deadline` has no request receipt time to anchor its default against,
so it is anchored on the current state's transition time instead, which
means a legacy row's budget restarts at each transition. `attempts` and
`last_progress` are recorded but not yet consumed; the retry bound and
the node-local reaper which reads them arrive with the rest of
`docs/plans/PLAN-agent-operation-deadlines.md`.

Namespace keys used to be anonymous entries inside that row's `keys`
JSON dict. They are now objects in their own right, so that a key can
carry an expiry, be listed, reaped, and (in a later release) scoped.
The static row is one per key, and the hash and nonce live in the
attribute row because rotating a key replaces both.

Version 2 of `namespace_keys` is a one-shot data migration rather than
a schema change: `sf-ctl ensure-mariadb-schema` reads every
`namespace_attributes.keys` blob and fans each `nonced_keys` entry out
into a static row, an attribute row, and an `object_states` row in
state `created`. Hashes, nonces and expiries are copied verbatim, so
tokens issued before the upgrade keep working; scopes and provenance
are NULL. Expired keys are migrated too and removed by the reaper on
its next pass. The migration is idempotent — keys which already have a
row are skipped — so it is safe to re-run.

The legacy JSON column is deliberately left untouched by the
migration, exactly as the `node_daemon_states` migration left
`node_attributes.daemon_states` in place. One consequence is worth
knowing before an upgrade: **rolling back to a pre-upgrade release
revives the JSON column**, which still holds every key that existed
before the migration, but keys created or rotated after the migration
exist only in the new tables and will be invisible to the rolled-back
code.

#### Federation Abuse Resistance Tables

Two tables in the federated exchange path hold no objects at all. They
exist to make an unauthenticated endpoint safe to expose, so they are
plain tables with no UUID, no state and no attribute row:

| Table | Purpose | Fields |
|-------|---------|--------|
| `federation_replay` | One row per identity token exchanged through one rule, so a token cannot be exchanged twice | token_id + rule_uuid (composite PK), expires_at (indexed) |
| `federation_rate_limits` | Attempts per source address per minute | source + window_start (composite PK), attempts, window_start (indexed) |

Both grow with traffic rather than with the size of the cluster, and
both are swept by the cluster daemon: `reap_federation_replay` removes
rows whose `expires_at` has passed (a token that can no longer be
validated cannot be replayed, so the record has no further use), and
`reap_federation_rate_limits` removes closed counting windows. Neither
needs operator attention; the reaper counters are visible on the
database daemon's metrics port.

#### Node Identity and UUID Persistence

Each node in the cluster is assigned a real UUID (UUID version 4) when it
first registers with the cluster. Previously, nodes used their FQDN as a
fake UUID, but all nodes now have proper UUIDs stored in the `nodes`
MariaDB table with the FQDN as a separate uniquely-indexed column.

To avoid an FQDN-to-UUID database lookup on every daemon startup, the
node UUID is persisted locally to `{STORAGE_PATH}/node_uuid` (typically
`/srv/shakenfist/node_uuid`). On subsequent daemon starts, the UUID is
read from this local file for a direct database lookup by primary key.

The node UUID can also be set explicitly via the `SHAKENFIST_NODE_UUID`
environment variable or the `NODE_UUID` configuration field, which takes
precedence over the local file. This is useful for disaster recovery
scenarios where local storage has been lost but the node's UUID is known.

The lookup precedence order is:

1. `NODE_UUID` configuration field / `SHAKENFIST_NODE_UUID` environment
   variable
2. Local file at `{STORAGE_PATH}/node_uuid`
3. FQDN-based lookup in the `nodes` table (fallback)

If the persisted UUID does not match the current node's FQDN, it is
ignored and the FQDN-based fallback is used. This guards against stale
UUID files left over from a previous node installation.

Each attribute table follows the same pattern — typed scalar columns
for hot-path fields, JSON columns for complex structures, and one
indexed FK column per parent — for example:

```sql
CREATE TABLE node_attributes (
    uuid UUID PRIMARY KEY,
    last_seen DOUBLE,
    installed_version VARCHAR(64),
    -- Complex structures as JSON
    roles JSON,
    daemons JSON,
    metrics JSON
);
```

Cached lists of *child* object UUIDs are deliberately not stored on
the parent attribute table — querying the child table by an indexed
FK column is the source of truth. The last two such caches
(`network_attributes.networkinterfaces` and
`instance_attributes.interfaces`) have been removed; see
`docs/plans/PLAN-sql-pushdown-filtering.md`.

This approach:

- **Avoids wide generic tables**: Each type has exactly the columns it needs
- **Enables proper typing**: Native SQL types instead of JSON everywhere
- **Supports efficient indexes**: Can index frequently-queried columns
- **Keeps queries simple**: No joins needed for common operations

### Abstract Base Classes

Abstract base classes like `DatabaseBackedObject` and `ManagedExecutable` do
not get their own tables. Only concrete classes that are actually instantiated
have tables. For example:

- `ManagedExecutable` (abstract) - no table
- `DnsMasq` (concrete, inherits ManagedExecutable) - gets `dnsmasq` table

### Pydantic Models as Schema Source

Each table is defined by a Pydantic model that serves as the single source of
truth:

```python
from typing import Annotated
from pydantic import BaseModel, ConfigDict, UUID4
from shakenfist.schema.sqlalchemy import SQLIndex, SQLNativeUUID

class DnsMasqData(BaseModel):
    """Schema for DnsMasq static values in MariaDB."""
    model_config = ConfigDict(frozen=True)

    uuid: Annotated[UUID4, SQLNativeUUID()]
    namespace: Annotated[str, SQLIndex()]
    owner_type: Annotated[str, SQLIndex()]
    owner_uuid: Annotated[str, SQLIndex()]
    version: int
    provide_dhcp: bool
    provide_dns: bool
```

The table is then generated from this model:

```python
from shakenfist.schema.sqlalchemy import pydantic_to_sqlalchemy_table

table = pydantic_to_sqlalchemy_table(
    DnsMasqData, 'dnsmasq', metadata,
    primary_key_field='uuid', include_id_column=False
)
```

### Adding New Attributes

When adding a new attribute to an object type:

**For shared attributes (DatabaseBackedObject level):**

1. Consider if it belongs in an existing shared table (like `object_states`)
2. If it's a new shared concept, create a new shared table

**For type-specific attributes:**

1. Add the field to the Pydantic model
2. `ALTER TABLE` to add the column (with default if needed)
3. Bump the object's version number
4. Add an upgrade step (can be no-op if column has a DB default)

### Object Version Upgrades

Objects have version numbers that track schema changes. When an object is
read from the database with an older version:

1. **Lazy upgrade**: The `upgrade_pydantic_data()` method applies upgrade steps
2. **Persistence**: If the cluster minimum version equals current version, the
   upgraded data is written back to MariaDB
3. **Background migration**: A future background worker will upgrade objects
   that are never read

This allows rolling upgrades without requiring all objects to be migrated
immediately.

## Schema System

Shaken Fist uses Pydantic models for schema definition. These models serve
multiple purposes:

1. **Validation**: Ensuring data conforms to expected types and constraints
2. **Serialization**: Converting between Python objects and JSON payloads
3. **SQL Generation**: Automatically generating SQLAlchemy tables for MariaDB

### Pydantic Models

Schema definitions live in `shakenfist/schema/`. For example, cluster operations
have their schemas defined in `shakenfist/schema/operations/`.

A typical schema looks like:

```python
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field, UUID4

class model_tasks(Enum):
    verify_size_and_checksum = 1
    ensure_local = 2

class model(BaseModel):
    uuid: UUID4
    node_uuid: str
    blob_uuid: UUID4
    priority: PRIORITY
    request_id: Optional[str]
    tasks: List[model_tasks]
    version: int = Field(ge=1, le=1)
```

### SQLAlchemy Table Generation

The `shakenfist.schema.sqlalchemy` module provides utilities to automatically
convert Pydantic models to SQLAlchemy tables. This keeps the schema definition
in one place and avoids hand-writing SQL.

#### Basic Usage

```python
from shakenfist.schema.sqlalchemy import pydantic_to_sqlalchemy_table
import sqlalchemy as sa

metadata = sa.MetaData()
table = pydantic_to_sqlalchemy_table(
    MyModel,
    'my_table',
    metadata,
    primary_key_field='uuid'
)
```

#### Type Mapping

Python types are mapped to SQL column types:

| Python Type | SQL Type |
|-------------|----------|
| `str` | `VARCHAR(255)` |
| `int` | `BIGINT` |
| `float` | `DOUBLE` |
| `bool` | `BOOLEAN` |
| `bytes` | `LARGEBINARY` |
| `UUID` | `CHAR(36)` |
| `Enum` | `VARCHAR(64)` |
| `IPv4Address` | `INET4` (MariaDB-specific) |
| `list`, `dict`, nested models | `LONGTEXT` (JSON) |
| `Optional[X]` | Nullable column of type X |

### Index Annotations

Indexes can be defined directly in the Pydantic model using Python's
`Annotated` types. This keeps index definitions co-located with the schema.

#### Single-Column Indexes

Use `SQLIndex()` or `SQLUniqueIndex()` markers:

```python
from typing import Annotated
from pydantic import BaseModel
from shakenfist.schema.sqlalchemy import SQLIndex, SQLUniqueIndex

class User(BaseModel):
    uuid: Annotated[str, SQLIndex()]           # Creates idx_users_uuid
    email: Annotated[str, SQLUniqueIndex()]    # Creates uidx_users_email
    name: str                                   # No index
```

#### Compound Indexes

For indexes spanning multiple columns, use the model's configuration:

```python
from pydantic import BaseModel, ConfigDict

class Event(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            'sql_indexes': [
                ('object_type', 'object_uuid'),  # Compound index
                ('timestamp',),                   # Single column via config
            ]
        }
    )

    object_type: str
    object_uuid: str
    timestamp: float
    message: str
```

#### Generated Index Names

Index names follow a predictable pattern:

- Single-column: `idx_{table}_{column}` or `uidx_{table}_{column}` (unique)
- Compound: `idx_{table}_{col1}_{col2}_{...}`

### Table Lifecycle

The `ensure_table_exists()` function handles idempotent table creation:

```python
from shakenfist.schema.sqlalchemy import (
    pydantic_to_sqlalchemy_table,
    ensure_table_exists
)

# Create table definition
table = pydantic_to_sqlalchemy_table(MyModel, 'my_table', metadata)

# Create table and indexes in database (idempotent)
ensure_table_exists(engine, table)
```

### Schema Comparison

To detect schema drift between the Pydantic model and the database:

```python
from shakenfist.schema.sqlalchemy import compare_schemas

differences = compare_schemas(engine, table)
# Returns: {
#     'missing_columns': [...],  # In model but not in DB
#     'extra_columns': [...],    # In DB but not in model
#     'type_mismatches': [...]   # Different types
# }
```

## Object State Storage

Object state (e.g., "created", "deleted", "error") is stored in a dedicated
MariaDB table for improved query performance. Access is routed through the
database service's gRPC interface for all daemons except the database daemon
itself.

### The object_states Table

The `object_states` table stores state for all object types:

```python
from typing import Annotated, Optional
from pydantic import BaseModel, ConfigDict, Field
from shakenfist.schema.sqlalchemy import SQLIndex, SQLUniqueIndex

class ObjectState(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            'sql_indexes': [
                ['object_type', 'state_value'],  # Efficient queries by type+state
            ]
        }
    )

    object_uuid: Annotated[str, SQLUniqueIndex(), Field(max_length=36)]
    object_type: Annotated[str, SQLIndex(), Field(max_length=32)]
    state_value: Annotated[str, SQLIndex(), Field(max_length=32)]
    update_time: float
    message: Optional[str] = None
```

### State Class

The `State` class is a Pydantic model that replaces the original `baseobject.State`
class. It provides the same interface for backwards compatibility:

```python
from shakenfist.schema.object_state import State

state = State(value='created', update_time=time.time(), message='optional msg')
print(state.value)        # 'created'
print(state.update_time)  # 1234567890.123
print(state.obj_dict())   # {'value': 'created', 'update_time': 1234567890.123}
```

## IPAM Reservation Storage

IPAM (IP Address Manager) reservations are stored in MariaDB for atomic address
allocation. This provides:

- **Atomic reservation**: Uses database uniqueness constraints to prevent race
  conditions when multiple nodes try to allocate the same address
- **Efficient queries**: Indexes on ipam_uuid and address for fast lookups
- **Deletion halo**: Supports the deletion-halo pattern where recently released
  addresses are temporarily unavailable to prevent reuse conflicts

### The ipam_reservations Table

The `ipam_reservations` table uses a composite primary key on (ipam_uuid, address):

```python
from ipaddress import IPv4Address

class IPAMReservation(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            'sql_indexes': [
                ['ipam_uuid', 'address'],      # Composite unique key
                ['user_type', 'user_uuid'],    # Query by user
            ]
        }
    )

    ipam_uuid: Annotated[str, SQLIndex(), Field(max_length=36)]
    address: Annotated[IPv4Address, SQLIndex()]  # Maps to INET4 column
    reservation_type: ReservationType            # Enum stored as VARCHAR
    user_type: Optional[str] = Field(default=None, max_length=32)
    user_uuid: Optional[str] = Field(default=None, max_length=36)
    reserved_at: float
    comment: Optional[str] = None
```

The `address` field uses Python's `ipaddress.IPv4Address` type, which maps to
MariaDB's `INET4` column type. This provides efficient 4-byte storage and native
IP address comparison operations.

### Reservation Types

IPAM supports several reservation types:

| Type | Description |
|------|-------------|
| `network` | The network address (e.g., 10.0.0.0) |
| `broadcast` | The broadcast address (e.g., 10.0.0.255) |
| `gateway` | The gateway address for the network |
| `floating` | A floating IP that can be moved between instances |
| `routed` | A routed IP address for external connectivity |
| `instance` | An IP assigned to an instance interface |
| `deletion-halo` | A recently-released address in the deletion halo |

## Upload Object Storage

Upload objects (temporary objects that receive streamed data during artifact
creation) are stored in MariaDB. This provides:

- **Efficient iteration**: Fast queries for cleanup of stale uploads
- **Node-based lookups**: Indexed queries to find uploads by node for routing

### The uploads Table

The `uploads` table stores static values for upload objects:

| Column | Type | Description |
|--------|------|-------------|
| uuid | UUID | Primary key - the upload's unique identifier |
| node | VARCHAR(255) | The node where the upload data is stored |
| created_at | DOUBLE | Unix timestamp when the upload was created |
| version | INTEGER | Object version number |

Indexes:
- Primary key on `uuid`
- Index on `node` for efficient routing of upload requests
- Index on `created_at` for finding old uploads during cleanup

## Best Practices

### Schema Evolution

When adding new fields:

1. Add the field to the Pydantic model with a default value
2. Use `Optional[X]` for fields that may not exist in old data
3. Include a version field to track schema versions
4. Handle missing fields gracefully in code

### Rolling Deployments

During rolling upgrades where nodes may run different versions:

1. New fields should be optional until all nodes are upgraded
2. Old code should ignore unknown fields
3. Use version fields to detect and handle schema differences

### Performance Considerations

- Use indexes for fields that are frequently queried
- Prefer compound indexes for queries that filter on multiple columns
- Keep JSON/LONGTEXT fields for data that doesn't need indexing
- Use MariaDB for data requiring complex queries
