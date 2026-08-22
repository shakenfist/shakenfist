# PLAN: Fix cluster_operation_targets UNIQUE constraint

## Status

Complete.

The composite constraint is in the tree, in the
`cluster_operation_targets` table definition and its migration.
The root cause was confirmed from the CI artifact bundle for run
`25727839217` (Guests job, 2026-05-12), with the diagnostic
instrumentation added in commit `5d0bf73c`. See "Evidence" below.

## Problem

The forbidden-string scanner in CI has been intermittently failing on
`Recreating not okay network on hypervisor` for months. The post-
`86aacb19` change to `Network.is_okay()` was meant to close the
last_cluster_operation race; commit `5d0bf73c` added instrumentation to
distinguish "bridge was torn down unexpectedly" from "bridge create
failed silently". The 2026-05-12 bundle shows neither -- the bridge was
**never created on the affected hypervisor in the first place**, and
the maintainer was the one creating it for the first time. The race is
between `sf-net`'s maintainer and the `node_inst_net_iface_op`
(hot-plug interface) cluster operation.

## Root cause

The `cluster_operation_targets` table is declared with
`unique=True` on `operation_uuid`
(`shakenfist/mariadb.py:639-640`):

```python
sa.Column('operation_uuid', sa.String(36),
          nullable=False, unique=True),
```

This makes it physically impossible to record more than one target row
per operation. But the design (commit message of `86aacb19` and the
`target_fields: ClassVar[dict[str, ObjectType]]` declarations on
operation schemas) explicitly **requires** multiple target rows per op
for multi-target operations.

`_direct_create_cluster_operation_target`
(`shakenfist/mariadb.py:5327-5361`) catches `IntegrityError` and
returns `True` silently:

```python
except IntegrityError:
    # Duplicate operation_uuid — already recorded, which is fine.
    # The UNIQUE constraint on operation_uuid ensures idempotency.
    return True
```

So for any op with N>1 targets, only the **first** target row is
persisted; the remaining N-1 are silently dropped. The comment claims
idempotency; in reality it drops semantically-different rows because
the column the design wants to be unique-per-target-row is
`(operation_uuid, target_object_type, target_uuid)`, not just
`operation_uuid`.

### Affected operation schemas

`grep -A6 'target_fields: ClassVar' shakenfist/schema/operations/`
shows four multi-target ops. For each, only the **first** declared
target is recorded; the rest are dropped:

| Schema                       | Targets (dict order)                    | Recorded | Dropped                |
| ---------------------------- | --------------------------------------- | -------- | ---------------------- |
| `node_inst_net_iface_op`     | instance, network, interface            | instance | **network, interface** |
| `artifact_fetch_op`          | artifact, instance                      | artifact | instance               |
| `net_iface_ip_op`            | network, interface                      | network  | interface              |
| `net_iface_op`               | network, interface                      | network  | interface              |

The hot-plug op (`node_inst_net_iface_op`) is the one tripping the CI
guard, because the **network** target row is the one
`Network.is_okay()` reads to defer the maintainer. With that row
missing, `has_pending_cluster_operation(network)` returns False, the
maintainer falls through to `is_created()`, the bridge is genuinely
absent on the target hypervisor (the hot-plug op hasn't reached
`create_on_hypervisor` yet), and the forbidden-string event fires.

The bridge is then created idempotently -- first by the maintainer at
poll time, then later (no-op) by the hot-plug op's
`create_on_hypervisor` once its `depends_on` net_op completes. There
is no functional breakage of the hot-plug -- only the cosmetic
forbidden-string trip. But the same schema bug would cause real bugs
for `is_okay()` defers on the other three ops as well.

## Evidence (CI run 25727839217)

Timeline reconstructed from
`bundle-functional-cluster-guests/bundle/t-SqLSw-{3,5}/var/log/syslog`:

| Time         | Node | What                                                                                                            |
| ------------ | ---- | --------------------------------------------------------------------------------------------------------------- |
| 10:46:58.886 | sf-3 | API receives `POST /instances/ef251675.../interfaces` (hot-plug)                                                |
| 10:46:59.478 | sf-3 | NetworkInterface `d17c9638` row written, state=initial                                                          |
| 10:47:00.276 | sf-3 | net_op `c50f0867` (network_update_dnsmasq) enqueued                                                             |
| 10:47:00.986 | sf-3 | node_inst_net_iface_op `7295cbee` enqueued, targets={instance,network,interface}                                |
| 10:47:01.074 | sf-5 | Op `7295cbee` execution deferred 15s, `waiting_on=[net_op c50f0867]`                                            |
| 10:47:13.014 | sf-5 | sf-net maintainer: `ip link show br-vxlan-35072f` → exit 1 "does not exist"                                     |
| 10:47:13.111 | sf-5 | sf-net emits `network not ok, is not created` (proves `has_pending_cluster_operation` returned False)           |
| 10:47:13.174 | sf-5 | sf-net emits **`recreating not okay network on hypervisor`** -- forbidden string                                |
| 10:47:13.239 | sf-5 | sf-net runs `create_on_hypervisor`, bridge created cleanly                                                      |
| 10:47:17.002 | sf-5 | Op `7295cbee` state→executing                                                                                   |
| 10:47:17.063 | sf-5 | Hot-plug op runs `create_on_hypervisor` (idempotent, bridge already up)                                         |
| 10:47:17.763 | sf-5 | Op `7295cbee` state→complete                                                                                    |

The op was in `queued` state from 10:47:00.986 through 10:47:17.002 --
a 16-second window in which `has_pending_cluster_operation(network)`
**should** have returned True and made `Network.is_okay()` short-circuit
to True. It returned False. The only consistent explanation is that
the `cluster_operation_targets` row for `target_object_type=network,
target_uuid=88eb4715...` was never persisted.

The schema bug (`unique=True` on `operation_uuid`) is the proximate
cause: `enqueue_cluster_operation` iterates `target_fields` in
declaration order `{instance, network, interface}`, calls
`mariadb.create_cluster_operation_target` three times for the same
`operation_uuid=7295cbee...`, the second and third inserts raise
`IntegrityError`, and the catch-and-return-True swallows them.

## Test gap

`shakenfist/tests/test_cluster_operation_targets.py:574`
(`test_hot_plug_enqueue_writes_three_target_rows`) **passes** today
because it mocks `mariadb.create_cluster_operation_target` directly --
it asserts the function was called three times with the right args
but never exercises the actual DB constraint. The constraint check
must be moved to an integration test against a real engine (or at
minimum an in-memory SQLAlchemy engine with the same table
definition).

## Fix

### Schema change

Replace the column-level `unique=True` on `operation_uuid` with a
composite uniqueness over the three columns that together identify a
unique target row:

```python
sa.Table(
    'cluster_operation_targets', metadata,
    sa.Column('sequence_number', sa.BigInteger(),
              primary_key=True, autoincrement=True),
    sa.Column('operation_uuid', sa.String(36), nullable=False),
    sa.Column('operation_type', sa.String(64), nullable=False),
    sa.Column('target_object_type', sa.Enum(ObjectType),
              nullable=False),
    sa.Column('target_uuid', sa.String(36), nullable=False),
    sa.Column('created_at', sa.Double(), nullable=False),
    sa.UniqueConstraint(
        'operation_uuid', 'target_object_type', 'target_uuid',
        name='uq_cot_op_target'),
    sa.Index('idx_cot_target', 'target_object_type', 'target_uuid'),
    sa.Index('idx_cot_operation', 'operation_uuid'),
    sa.Index('idx_cot_created', 'created_at'),
)
```

The new `idx_cot_operation` preserves the fast lookup that the old
unique-on-`operation_uuid` constraint was providing as a side effect
(comment at `shakenfist/mariadb.py:633`).

### Migration

Bump `CLUSTER_OPERATION_TARGETS_VERSION` and add a migration step
that:
1. Drops the existing UNIQUE constraint on `operation_uuid`.
2. Adds the composite UNIQUE on `(operation_uuid, target_object_type,
   target_uuid)`.
3. Adds the new non-unique index on `operation_uuid`.

The existing table will have at most one row per `operation_uuid`, so
no data fix-up is required. The compound UNIQUE will accept any new
rows after migration.

### Insert path

Keep the `IntegrityError → True` early-return for true duplicates (the
composite UNIQUE only fires on actual duplicate `(op, target_type,
target_uuid)` triples, which **is** idempotency). Update the comment to
reflect the new constraint.

### Backfill for in-flight operations

Operations currently in flight at upgrade time will have only one
target row each, so multi-target ops in flight at the moment of
upgrade will continue to leak through the gate one more time. Two
options:

1. **Do nothing**: the next maintainer poll for any affected network
   will see the bridge missing and recreate it -- exactly the
   current bug, but only during the upgrade window.
2. **Replay**: on startup of the database daemon after migration, scan
   in-flight ops and re-call the `enqueue_cluster_operation` target-
   write loop for them. Adds complexity for a one-time window.

Recommend (1) for the upgrade. Document that the forbidden-string
trip is expected during the upgrade window of this fix.

### Test fix

Add an integration-style test in `test_cluster_operation_targets.py`
that uses a real SQLAlchemy engine (in-memory SQLite is fine for the
shape, MariaDB in CI for the actual constraint) and asserts:

1. Enqueuing `node_inst_net_iface_op` results in **three rows** in
   `cluster_operation_targets`, one per declared target.
2. `has_pending_cluster_operation_target(NETWORK, network_uuid)`
   returns True while the op is queued/preflight/executing.
3. Replays of the same op (idempotency check) do not produce
   duplicate rows -- the composite UNIQUE handles this.

The existing mock-based test is fine to keep as a unit test of the
schema's `target_fields` declaration and the call-site fan-out; mark
it as such in the docstring.

## Why this didn't show up earlier

* Single-target ops (the vast majority) are unaffected. The four
  multi-target ops are all relatively recent additions in the
  cluster_operation_targets era.
* For `node_inst_net_iface_op` specifically, the **instance** target
  *is* recorded -- so any gate that reads the instance's pending-op
  list sees the op correctly. The bug is invisible from the instance
  side and only manifests from the network/interface side.
* The unit test asserts call counts, which pass regardless of what the
  DB does with the writes.

## Risk

Low for the fix itself: removing an overly-strict constraint with a
correct replacement, idempotency preserved, no callers depended on the
old constraint outside the DB write helper.

The only callers of `cluster_operation_targets` that read rows are:

* `get_latest_cluster_operation_target` -- unaffected, picks one row.
* `has_pending_cluster_operation_target` -- unaffected, EXISTS query.
* `get_cluster_operation_targets_for_object` -- already designed to
  return multiple rows (per-object history).
* `delete_cluster_operation_targets_for_object` -- unaffected.
* `delete_stale_cluster_operation_targets` -- unaffected.

None assume one-row-per-operation_uuid; they all key on `target_uuid`
or `operation_uuid` and tolerate multiple rows.

## Out of scope

* The 5d0bf73c instrumentation can stay for now; once this fix is in,
  the next failure (if any) will be a genuinely different race and the
  debug events still help.
* The TOCTOU race in `_instance_delete` (host_networks snapshot vs
  `delete_on_hypervisor`) that I initially suspected is **not** the
  cause here, but it remains theoretically possible. Leave that for a
  separate plan if it ever fires under the new constraint.
