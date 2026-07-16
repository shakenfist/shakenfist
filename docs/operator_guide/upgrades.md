# Upgrades

Shaken Fist supports online upgrades natively -- when an object is read from
MariaDB with an older version number, the object is upgraded silently to the
newest version. If all nodes in your cluster are running a version of Shaken
Fist which supports this newest version, the upgraded object is then written
back to MariaDB. If not all nodes in the cluster support the new version, the
new version is simply used in memory by the node which did the upgrade. This
means it is safe to perform a rollout across a cluster without downtime,
although you might see small transient failures such as single API requests
failing as processes restart.

You should note however that "all nodes" includes nodes in non-running states
such as ERROR and MISSING. The only state which is excluded from the check is
DELETED. Therefore, in order for online upgrades to work correctly, it is
important that you delete nodes in an ERROR or MISSING state that you are
confident will not return to the cluster. This is because nodes can return
from ERROR or MISSING at the end of planned maintenance, and might be running
and older version of Shaken Fist upon their return than other members of the
cluster.

## Upgrade process

First off, upgrade the python packages in each node's virtualenv manually. This
is explicitly a non-goal of our deploy tooling as we believe different
deployments will have different strategies for performing this step. Naively,
a good first approach is simple to run this command on each node:

```
sudo /srv/shakenfist/venv/bin/pip install --upgrade shakenfist
```

Which will pull in all the relevant other python packages it requires.

Then simply re-run your deployment playbook as you did when you first
installed (see [Installation](installation.md)) and the cluster will upgrade.

If you deploy with the [`shakenfist.shakenfist` Ansible
collection](https://github.com/shakenfist/shakenfist/tree/develop/deploy/collection),
re-running the playbook is **restart-on-change**: the `node` role restarts a
node's `sf-*` daemons only when the installed code, `/etc/sf/config`, or a
systemd unit actually changed on that node. A redeploy on a day nothing moved
leaves the running cluster undisturbed, so it is safe to run the deploy on a
schedule; on a day the code did move, the daemons restart to pick it up (a
built-in daily upgrade-path test). Code changes are detected by hashing the
installed package files, not the wheel, so a rebuilt-but-identical wheel does
not trigger a spurious restart. See the collection's *Idempotence and
restart-on-change* section for details. This automates, per node, the same
restart the manual procedure below performs by hand.

## MariaDB schema migrations

Starting with v0.8, Shaken Fist uses MariaDB to store object state data. The
MariaDB schema is versioned; migrations must be applied by an operator (or
operator automation) before starting `sf-database`.

Each MariaDB table has a version number tracked in the `schema_versions` table.
When the cluster is deployed or upgraded, the `sf-ctl ensure-mariadb-schema`
command must be run on a node with direct database access to:

1. Perform a compatibility check against the server requirements (MariaDB
   not MySQL, version >= 10.6.0, InnoDB engine, utf8mb4 charset and
   collation) — the command refuses to proceed if any check fails
2. Create any missing tables
3. Apply any pending schema migrations to bring tables up to the current
   version

`sf-database` also performs the same compatibility check at startup and
will refuse to start if the server is incompatible. Importantly, if
`sf-ctl ensure-mariadb-schema` has not been run after an SF upgrade that
includes schema changes, `sf-database` will refuse to start with a clear
schema-version mismatch error that names the command to run. This replaces
the old behaviour where migrations ran silently inside the daemon on
startup.

See [MariaDB compatibility requirements](database.md#mariadb-compatibility-requirements)
in the database reference for the full list of server requirements.

### Manual schema verification

You can manually check or apply schema migrations by running on a node
with direct database access:

```
sudo /srv/shakenfist/venv/bin/sf-ctl ensure-mariadb-schema
```

This will output the current version of each table and whether any
migrations were applied. For example:

```
Table 'object_states' is up to date (version 1)
MariaDB schema verified.
```

Or if a migration was needed:

```
Migrated table 'object_states' from version 1 to 2
MariaDB schema verified.
```

### Known migration notes

#### cluster_operation_targets v1 to v2

The v1 schema declared `operation_uuid` as a column-level `UNIQUE`,
which prevented multi-target operations (e.g. the hot-plug interface
op, which targets an instance, a network, and an interface) from
recording more than one target row. The v2 migration replaces that
constraint with a composite `UNIQUE(operation_uuid, target_object_type,
target_uuid)` and adds a non-unique index `idx_cot_operation` for
single-column lookups.

Operations already in flight at the moment the migration runs will
have only their first declared target row in the table; their
remaining target rows were silently dropped under v1 and cannot be
reconstructed. The practical effect is that
`Network.is_okay()`'s pending-operation gate may transiently miss
one in-flight op per affected network during the upgrade window,
and the network maintainer can race the queue worker once -- which
manifests as the "Recreating not okay network on hypervisor" event
firing in syslog for that network. The recreate is idempotent with
the queued operation's own `create_on_hypervisor`, so there is no
functional breakage; only the audit event.

Operations enqueued after the migration completes record all of
their targets correctly, so the gate behaves as designed once the
in-flight v1 ops drain.

### Database service architecture

Only the database node has direct access to MariaDB credentials. All other
nodes access MariaDB data through a gRPC database microservice that runs on
the database node. This means:

* Schema migrations only need to run on the database node
* Other nodes do not require MariaDB credentials
* `sf-ctl ensure-mariadb-schema` must be run (or re-run after an upgrade)
  before starting `sf-database`
* The database service must be running before other daemons can access
  state data

## Rolling upgrade with drain

The procedure below achieves zero-downtime upgrades for the `sf-api` tier
by exploiting the readiness drain built into every `sf-api` worker. It
assumes you have a load balancer probing `/readyz` on port `13000` as
described in [Load Balancing](load_balancing.md). Without an LB watching
`/readyz`, stopping `sf-api` is a hard cut rather than a graceful drain.

### Before you start — schema migrations

If the release you are installing includes schema changes, you must apply
them **before** rolling any nodes to the new build. Run the following on a
node with direct MariaDB access (i.e. `MARIADB_HOST` configured — typically
a database-tier node):

```bash
sudo /srv/shakenfist/venv/bin/sf-ctl ensure-mariadb-schema
```

`sf-database` refuses to start if the schema version does not match what
its build expects, so applying migrations first lets you upgrade the
database tier without downtime before touching the API or hypervisor nodes.
See [MariaDB schema migrations](#mariadb-schema-migrations) above and the
[database reference](database.md#administrative-commands) for full details.

### Per-node procedure

Upgrade **one node at a time**. Confirm that each node is healthy before
moving to the next.

**1. Stop `sf-api` on the node.**

```bash
sudo systemctl stop sf-api
```

Systemd sends SIGTERM. On receipt, the `sf-api` worker immediately flips
`/readyz` to return `503 Service Unavailable`. The load balancer detects this
on its next health-check probe and stops sending new requests to this node.
The worker keeps serving for the drain grace period (`API_DRAIN_GRACE`,
default 25 s) and only then begins gunicorn's graceful shutdown. Note that
gunicorn's `--graceful-timeout` countdown starts at the SIGTERM (not when the
worker finally shuts down), so the two windows overlap rather than stack: with
`--graceful-timeout 55 s` and a 25 s drain, in-flight requests have the
remaining ~30 s to finish before gunicorn force-closes them. The systemd
`TimeoutStopSec` of 70 s exceeds the graceful timeout and caps the whole
sequence. For most workloads the node is out of rotation and quiet well within
the grace period.

**2. Upgrade the node's virtualenv.**

Follow the manual venv-upgrade procedure described in
[Upgrade process](#upgrade-process) above, for example:

```bash
sudo /srv/shakenfist/venv/bin/pip install --upgrade shakenfist
```

**3. Restart SF services on the node.**

For hypervisor nodes restart all services:

```bash
sudo systemctl restart sf-api sf-cleaner sf-cluster sf-net sf-nodelock \
    sf-privexec sf-queues sf-resources sf-sidechannel sf-transfers
```

For a database-tier node restart the database service first:

```bash
sudo systemctl restart sf-database
sudo systemctl restart sf-api sf-cleaner sf-cluster sf-net sf-nodelock \
    sf-privexec sf-queues sf-resources sf-sidechannel sf-transfers
```

!!! note
    If the `sf-cluster` elected leader is on the node you are rolling, its
    cluster lock lease will expire (within 60 s of the daemon stopping).
    A standby node will then win the election and take over cluster
    maintenance automatically. See [Locks](locks.md) for the lease and
    failover details.

**4. Confirm the node is healthy before moving on.**

Poll `/readyz` until it returns `200 OK`:

```bash
curl -sf http://<node-ip>:13000/readyz && echo "ready"
```

`/readyz` returns `200` once the worker has successfully contacted
`sf-database`. The load balancer will return the node to rotation on its
next successful probe.

You can also probe the `sf-database` gRPC health endpoint directly:

```bash
grpc-health-probe -addr=<sf-database-host>:13005
```

`SERVING` means `sf-database` can reach MariaDB and the node is ready for
the next node upgrade. See the
[database reference](database.md#monitoring-sf-database-with-grpc-health-probe)
for details.