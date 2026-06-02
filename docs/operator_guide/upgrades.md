# Upgrades

Shaken Fist supports online upgrades natively -- when an object is read from
etcd that is an old version, the object is upgraded silently to the newest
version. If all nodes in your cluster are running a version of Shaken Fist
which supports this newest version, the upgraded object is then written back
to etcd. If not all nodes in the cluster support the new version, the new
version is simply used in memory by the node which did the upgrade. This means
it is safe to perform a rollout across a cluster without downtime, although
you might see small transient failures such as single API requests failing
as processes restart.

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
is explicitly a non-goal of our installer tooling as we believe different
deployments will have different strategies for performing this step. Naively,
a good first approach is simple to run this command on each node:

```
sudo /srv/shakenfist/venv/bin/pip install --upgrade shakenfist
```

Which will pull in all the relevant other python packages it requires.

Then simply re-run `getsf` as you did when you first installed and the cluster
will upgrade.

## MariaDB schema migrations

Starting with v0.8, Shaken Fist uses MariaDB to store object state data. The
MariaDB schema is versioned, and migrations are applied automatically during
cluster deployment via `getsf`.

Each MariaDB table has a version number tracked in the `schema_versions` table.
When the cluster is deployed or upgraded, the `sf-ctl ensure-mariadb-schema`
command runs automatically on an etcd_master node to:

1. Create any missing tables
2. Apply any pending schema migrations to bring tables up to the current version

### Manual schema verification

You can manually check or apply schema migrations by running on an etcd_master
node:

```
sudo /srv/shakenfist/venv/bin/sf-ctl ensure-mariadb-schema
```

This will output the current version of each table and whether any migrations
were applied. For example:

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

Only etcd_master nodes have direct access to MariaDB credentials. All other
nodes access MariaDB data through a gRPC database microservice that runs on
etcd_master nodes. This means:

* Schema migrations only need to run on etcd_master nodes
* Non-etcd_master nodes do not require MariaDB credentials
* The database service must be running before other daemons can access state data